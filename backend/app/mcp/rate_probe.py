"""实测 westock-mcp 各只读工具的限频：`uv run python -m app.mcp.rate_probe [工具名 ...]`。

绕过系统自己的限流器，直接连续调用，直到第一次返回「服务限频」；
再每 5 秒重试一次记录恢复时间；恢复后再连续调用一轮，看额度是逐步回补还是整窗口重置。
只测只读工具；会消耗配额，测完约 30 分钟内这些工具可能仍处于限频状态，不要在收盘后流水线前后运行。
"""

import asyncio
import json
import sys
import time

from app.mcp.client import McpSession, extract_payload

PROBES: dict[str, dict] = {
    "data_finance": {"code": "sh600036", "type": "income", "num": 1},
    "data_kline": {"code": "sh600036", "period": "day", "limit": 5},
    "data_quote": {"codes": "sh600036"},
    "data_score": {"codes": "sh600036"},
    "data_risk": {"codes": "sh600036"},
    "data_dividend": {"code": "sh600036"},
    "tool_filter": {"expression": "PE_TTM < 5", "limit": 5},
    "tool_label": {"label": "ST"},
    "data_changedist": {"type": "0"},
}
MAX_CALLS = 60
MAX_BURST_SECONDS = 120
RECOVER_POLL = 5
RECOVER_MAX = 240


async def _once(sess: McpSession, tool: str, args: dict) -> str:
    """返回 ok / limited / error:<信息>。"""
    if sess._client is None:
        await sess._connect()
    try:
        result = await asyncio.wait_for(sess._client.call_tool(tool, args), timeout=60)
    except Exception as exc:
        await sess._close()
        return f"error:{type(exc).__name__}"
    text = json.dumps(extract_payload(result), ensure_ascii=False) if result.is_error else ""
    if not result.is_error:
        payload = extract_payload(result)
        if isinstance(payload, dict) and payload.get("ok") is False:
            text = str(payload.get("message"))
        else:
            return "ok"
    return "limited" if "限频" in text else f"error:{text[:80]}"


async def _burst(sess: McpSession, tool: str, args: dict) -> dict:
    start = time.monotonic()
    ok = 0
    outcome = "no_limit"
    while ok < MAX_CALLS and time.monotonic() - start < MAX_BURST_SECONDS:
        r = await _once(sess, tool, args)
        if r == "ok":
            ok += 1
            continue
        outcome = r
        break
    return {"ok": ok, "seconds": round(time.monotonic() - start, 1), "stopped_by": outcome}


async def _recover(sess: McpSession, tool: str, args: dict) -> float | None:
    start = time.monotonic()
    while time.monotonic() - start < RECOVER_MAX:
        await asyncio.sleep(RECOVER_POLL)
        if await _once(sess, tool, args) == "ok":
            return round(time.monotonic() - start, 1)
    return None


async def probe(tool: str, sess: McpSession) -> dict:
    args = PROBES[tool]
    first = await _burst(sess, tool, args)
    out = {"tool": tool, "first": first}
    if first["stopped_by"] != "limited":
        return out
    out["recover_seconds"] = await _recover(sess, tool, args)
    if out["recover_seconds"] is not None:
        out["second"] = await _burst(sess, tool, args)
    return out


async def main(tools: list[str]) -> None:
    sess = McpSession()
    results = []
    try:
        for tool in tools:
            r = await probe(tool, sess)
            results.append(r)
            print(json.dumps(r, ensure_ascii=False), flush=True)
    finally:
        await sess.close()
    with open("/tmp/mcp_rate_probe.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or list(PROBES)))
