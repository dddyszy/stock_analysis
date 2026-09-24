"""westock-mcp 调用器：streamable HTTP 会话、令牌桶限流、并发上限、重试与调用日志。"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

from app.core.config import get_settings
from app.db.models import McpCallLog
from app.db.session import session_scope
from app.mcp.errors import McpAuthError, McpBusinessError, McpError, McpRateLimited, McpTransportError
from app.mcp.oauth import oauth_manager

logger = logging.getLogger(__name__)


class ToolCaller(Protocol):
    async def call(self, tool: str, args: dict | None = None) -> Any: ...


class TokenBucket:
    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = rate
        self.capacity = capacity or max(rate, 1.0)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class AdaptiveLimiter:
    """按工具自适应限流：遇到"服务限频"立即冷却并减半速率，连续成功后缓慢提速。

    westock 的行情类工具实测约每分钟 3~5 次，且各工具额度不同，所以不写死速率。
    """

    def __init__(self, rate: float, min_rate: float = 1 / 30, max_cooldown: float = 180.0,
                 base_cooldown: float = 10.0, recover_after: int = 10) -> None:
        self.max_rate = rate
        self.min_rate = min_rate
        self.max_cooldown = max_cooldown
        self.base_cooldown = base_cooldown
        self.recover_after = recover_after
        self.bucket = TokenBucket(rate, capacity=3)
        self.cooldown_until = 0.0
        self.cooldown = base_cooldown
        self.success_streak = 0
        self.limited_count = 0

    async def acquire(self) -> None:
        wait = self.cooldown_until - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        await self.bucket.acquire()

    def on_limited(self) -> float:
        self.limited_count += 1
        self.success_streak = 0
        self.bucket.rate = max(self.min_rate, self.bucket.rate * 0.5)
        self.bucket.tokens = 0
        wait = self.cooldown
        self.cooldown_until = max(self.cooldown_until, time.monotonic() + wait)
        self.cooldown = min(self.max_cooldown, self.cooldown * 2)
        return wait

    def on_success(self) -> None:
        self.cooldown = self.base_cooldown
        self.success_streak += 1
        if self.success_streak >= self.recover_after:
            self.bucket.rate = min(self.max_rate, self.bucket.rate * 1.25)
            self.success_streak = 0

    def snapshot(self) -> dict:
        return {
            "rate_per_min": round(self.bucket.rate * 60, 2),
            "cooldown_left": max(0.0, round(self.cooldown_until - time.monotonic(), 1)),
            "limited_count": self.limited_count,
        }


RATE_LIMIT_MARKERS = ("限频", "rate limit", "too many requests")


def is_rate_limited(message: str) -> bool:
    low = message.lower()
    return any(m in low for m in RATE_LIMIT_MARKERS)


TRANSIENT_MARKERS = ("timeout", "deadline exceeded", "service error", "服务繁忙", "稍后重试", "connection reset")


def is_transient(message: str) -> bool:
    low = message.lower()
    return any(m in low for m in TRANSIENT_MARKERS)


def unwrap_payload(payload: Any) -> Any:
    """westock 工具统一返回 {ok, data, message}；ok=false 视为业务错误。"""
    if isinstance(payload, dict) and "ok" in payload:
        if not payload.get("ok"):
            raise McpBusinessError(str(payload.get("message") or "工具返回失败"))
        return payload.get("data")
    return payload


def extract_payload(result: Any) -> Any:
    if result.structured_content is not None:
        payload = result.structured_content
        # 部分服务把真实结果包在 {"result": ...} 里
        if isinstance(payload, dict) and set(payload.keys()) == {"result"}:
            payload = payload["result"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                pass
        return payload
    texts = [getattr(c, "text", "") for c in result.content if getattr(c, "type", "") == "text"]
    text = "".join(texts).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _log_call(tool: str, args: dict | None, ok: bool, duration_ms: int, error: str | None) -> None:
    try:
        with session_scope() as db:
            db.add(
                McpCallLog(
                    tool=tool,
                    args=json.dumps(args or {}, ensure_ascii=False)[:500],
                    ok=ok,
                    duration_ms=duration_ms,
                    error=(error or "")[:1000] or None,
                )
            )
    except Exception:  # 日志失败不影响业务
        logger.exception("写入 mcp_call_log 失败")


class _Limits:
    """进程内共享的限流器，所有会话共用：全局令牌桶 + 并发上限 + 每个工具的自适应限流。"""

    def __init__(self) -> None:
        s = get_settings()
        self.rate = s.mcp_rate_per_second
        self.bucket = TokenBucket(s.mcp_rate_per_second)
        self.semaphore = asyncio.Semaphore(s.mcp_max_concurrency)
        self.per_tool: dict[str, AdaptiveLimiter] = {}

    def tool(self, name: str) -> AdaptiveLimiter:
        if name not in self.per_tool:
            self.per_tool[name] = AdaptiveLimiter(self.rate)
        return self.per_tool[name]


_limits: _Limits | None = None


def _get_limits() -> _Limits:
    global _limits
    if _limits is None:
        _limits = _Limits()
    return _limits


def limiter_status() -> dict[str, dict]:
    if _limits is None:
        return {}
    return {name: lim.snapshot() for name, lim in _limits.per_tool.items()}


class McpSession:
    """一次连接内可以连续调用多个工具；连接失效或 token 过期时自动重连。"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: Client | None = None
        self._owner: asyncio.Task | None = None
        self._close_event: asyncio.Event | None = None
        self._last_status: int | None = None
        self._connect_lock = asyncio.Lock()

    async def _on_response(self, response: httpx2.Response) -> None:
        self._last_status = response.status_code

    async def _hold_connection(self, token: str, ready: asyncio.Future, close_event: asyncio.Event) -> None:
        # anyio 要求连接在同一个任务内进入和退出，所以由这个任务独占持有
        try:
            async with httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token}"},
                timeout=httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
                event_hooks={"response": [self._on_response]},
            ) as http:
                transport = streamable_http_client(self.settings.mcp_url, http_client=http)
                async with Client(
                    transport, mode="legacy", cache=None, read_timeout_seconds=self.settings.mcp_call_timeout
                ) as client:
                    self._client = client
                    ready.set_result(None)
                    await close_event.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            elif not isinstance(exc, asyncio.CancelledError):
                logger.debug("MCP 连接异常退出", exc_info=True)
        finally:
            self._client = None

    async def _connect(self, force_refresh: bool = False) -> None:
        await self._close()
        token = await oauth_manager.get_access_token(force_refresh=force_refresh)
        loop = asyncio.get_running_loop()
        ready: asyncio.Future = loop.create_future()
        self._close_event = asyncio.Event()
        self._last_status = None
        self._owner = asyncio.create_task(self._hold_connection(token, ready, self._close_event))
        try:
            await ready
        except BaseException as exc:
            status = self._last_status
            await self._close()
            if status == 401:
                raise McpAuthError("MCP 返回 401，token 无效") from exc
            raise McpTransportError(f"连接 MCP 失败: {exc} (http={status})") from exc

    async def _close(self) -> None:
        owner, event = self._owner, self._close_event
        self._owner, self._close_event = None, None
        if owner is None:
            return
        if event is not None:
            event.set()
        try:
            await asyncio.wait_for(owner, timeout=10)
        except BaseException:
            owner.cancel()
            logger.debug("关闭 MCP 会话出错", exc_info=True)

    async def close(self) -> None:
        await self._close()

    async def call(self, tool: str, args: dict | None = None) -> Any:
        limits = _get_limits()
        limiter = limits.tool(tool)
        args = {k: v for k, v in (args or {}).items() if v is not None}
        attempts = self.settings.mcp_max_retries + 1
        rate_limit_left = self.settings.mcp_rate_limit_retries
        auth_retried = False
        last_error: Exception | None = None
        attempt = 0
        while attempt < attempts:
            # 冷却等待放在并发槽之外，避免被限频的工具占满并发
            await limiter.acquire()
            async with limits.semaphore:
                await limits.bucket.acquire()
                started = time.monotonic()
                try:
                    async with self._connect_lock:
                        if self._client is None:
                            await self._connect()
                    self._last_status = None
                    result = await asyncio.wait_for(
                        self._client.call_tool(tool, args), timeout=self.settings.mcp_call_timeout + 5
                    )
                    duration = int((time.monotonic() - started) * 1000)
                    payload = extract_payload(result)
                    if result.is_error:
                        message = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
                        raise McpBusinessError(str(message)[:500])
                    data = unwrap_payload(payload)
                    _log_call(tool, args, True, duration, None)
                    limiter.on_success()
                    return data
                except McpBusinessError as exc:
                    _log_call(tool, args, False, int((time.monotonic() - started) * 1000), str(exc))
                    if is_rate_limited(str(exc)) and rate_limit_left > 0:
                        rate_limit_left -= 1
                        limiter.on_limited()
                        last_error = exc
                        continue
                    if is_rate_limited(str(exc)):
                        limiter.on_limited()
                        raise McpRateLimited(str(exc)) from exc
                    if is_transient(str(exc)) and attempt < attempts - 1:
                        attempt += 1
                        last_error = exc
                        await asyncio.sleep(min(2**attempt, 10))
                        continue
                    raise
                except McpAuthError as exc:
                    _log_call(tool, args, False, int((time.monotonic() - started) * 1000), str(exc))
                    if auth_retried:
                        raise
                    auth_retried = True
                    async with self._connect_lock:
                        await self._connect(force_refresh=True)
                    last_error = exc
                except Exception as exc:  # 网络、超时、协议错误
                    duration = int((time.monotonic() - started) * 1000)
                    status = self._last_status
                    _log_call(tool, args, False, duration, f"{type(exc).__name__}: {exc} (http={status})")
                    last_error = exc
                    if status == 401 and not auth_retried:
                        auth_retried = True
                        async with self._connect_lock:
                            await self._connect(force_refresh=True)
                        continue
                    async with self._connect_lock:
                        await self._close()
                    attempt += 1
                    if status == 429:
                        limiter.on_limited()
                    elif attempt < attempts:
                        await asyncio.sleep(min(2**attempt, 10))
        raise McpTransportError(f"调用 {tool} 失败: {last_error}")

    async def list_tools(self) -> list[dict]:
        async with self._connect_lock:
            if self._client is None:
                await self._connect()
        result = await self._client.list_tools()
        return [t.model_dump(by_alias=True, exclude_none=True) for t in result.tools]


class McpClient:
    """对外入口：`async with mcp_client.session() as s: await s.call(...)`。"""

    @asynccontextmanager
    async def session(self) -> AsyncIterator[McpSession]:
        sess = McpSession()
        try:
            yield sess
        finally:
            await sess.close()

    async def call(self, tool: str, args: dict | None = None) -> Any:
        async with self.session() as sess:
            return await sess.call(tool, args)


mcp_client = McpClient()

__all__ = [
    "McpClient",
    "McpSession",
    "McpError",
    "McpAuthError",
    "McpBusinessError",
    "McpTransportError",
    "ToolCaller",
    "mcp_client",
    "unwrap_payload",
]
