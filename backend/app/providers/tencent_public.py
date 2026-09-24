"""腾讯公开行情接口（无需授权）：历史 K 线与批量实时行情。

- K 线：web.ifzq.gtimg.cn fqkline，前复权单次最多 640 根，按日期分段拉取；北交所没有历史数据。
- 行情：qt.gtimg.cn，GBK 文本，字段以 "~" 分隔，一次可查多只。
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

import httpx

from app.mcp.client import AdaptiveLimiter
from app.providers.base import Bar, Quote
from app.providers.parsing import to_date, to_float

logger = logging.getLogger(__name__)

# 三个等价的 K 线地址轮换使用，各自独立限流
KLINE_URLS = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
)
QUOTE_URL = "https://qt.gtimg.cn/q="
PAGE_BARS = 640
PAGE_DAYS = 900  # 约 610 个交易日，保证单段不超过 640 根
QUOTE_BATCH = 60


THROTTLE_STATUS = (403, 429, 501, 503)
MAX_ATTEMPTS = 10

# 进程内共享：公开接口高频请求一段时间后会返回 501，按域名自适应降速
_limiters: dict[str, AdaptiveLimiter] = {}


def _limiter(host: str) -> AdaptiveLimiter:
    if host not in _limiters:
        _limiters[host] = AdaptiveLimiter(2.0, min_rate=0.5, max_cooldown=120.0, base_cooldown=15.0, recover_after=20)
    return _limiters[host]


_rotation = 0


def _next_urls() -> list[str]:
    global _rotation
    _rotation = (_rotation + 1) % len(KLINE_URLS)
    return [KLINE_URLS[(_rotation + i) % len(KLINE_URLS)] for i in range(len(KLINE_URLS))]


def public_limiter_status() -> dict[str, dict]:
    return {f"public:{h}": lim.snapshot() for h, lim in _limiters.items()}


class TencentPublicQuotes:
    def __init__(self, concurrency: int = 6) -> None:
        self._http = httpx.AsyncClient(timeout=20, headers={"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"})
        self._sem = asyncio.Semaphore(concurrency)

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, url: str | list[str], params: dict | None = None) -> httpx.Response:
        """url 为列表时，被限频后换下一个地址重试。"""
        urls = url if isinstance(url, list) else [url]
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            url = min(urls, key=lambda u: _limiter(httpx.URL(u).host).cooldown_until)
            limiter = _limiter(httpx.URL(url).host)
            await limiter.acquire()
            async with self._sem:
                try:
                    resp = await self._http.get(url, params=params)
                except (httpx.HTTPError, httpx.TimeoutException) as exc:
                    last = exc
                    await asyncio.sleep(min(2 ** attempt, 10))
                    continue
            if resp.status_code in THROTTLE_STATUS:
                last = httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                limiter.on_limited()
                continue
            resp.raise_for_status()
            limiter.on_success()
            return resp
        raise RuntimeError(f"公开行情接口多次失败: {last}")

    async def kline(self, code: str, period: str = "day", start: date | None = None, end: date | None = None,
                    fq: str = "qfq") -> list[Bar]:
        end = end or date.today()
        start = start or end - timedelta(days=PAGE_DAYS)
        bars: dict[date, Bar] = {}
        cursor_end = end
        span = timedelta(days=PAGE_DAYS if period == "day" else PAGE_DAYS * 5)
        while cursor_end >= start:
            cursor_start = max(start, cursor_end - span)
            param = f"{code},{period},{cursor_start.isoformat()},{cursor_end.isoformat()},{PAGE_BARS},{fq or ''}"
            resp = await self._get(_next_urls(), {"param": param})
            for b in _parse_kline(resp.json(), code, period, fq):
                if start <= b.dt <= end:
                    bars[b.dt] = b
            cursor_end = cursor_start - timedelta(days=1)
        return [bars[d] for d in sorted(bars)]

    async def quotes(self, codes: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for i in range(0, len(codes), QUOTE_BATCH):
            chunk = codes[i : i + QUOTE_BATCH]
            resp = await self._get(QUOTE_URL + ",".join(chunk))
            out.update(_parse_quotes(resp.content.decode("gbk", errors="ignore")))
        return out


def _parse_kline(doc: dict, code: str, period: str, fq: str) -> list[Bar]:
    node = (doc.get("data") or {}).get(code)
    if not isinstance(node, dict):
        return []
    rows = node.get(f"{fq}{period}") if fq else None
    if not rows:
        rows = node.get(period) or []
    out = []
    for r in rows:
        if not isinstance(r, list) or len(r) < 5:
            continue
        d = to_date(r[0])
        o, c, h, lo = (to_float(x) for x in r[1:5])
        if d is None or None in (o, c, h, lo):
            continue
        out.append(Bar(dt=d, open=o, high=h, low=lo, close=c, volume=to_float(r[5]) if len(r) > 5 else None))
    return out


def _parse_quotes(text: str) -> dict[str, Quote]:
    out: dict[str, Quote] = {}
    for line in text.split(";"):
        line = line.strip()
        if not line.startswith("v_") or '="' not in line:
            continue
        code = line[2 : line.index("=")]
        f = line[line.index('"') + 1 : line.rindex('"')].split("~")
        if len(f) < 47:
            continue
        dt = None
        if len(f[30]) >= 8:
            try:
                dt = datetime.strptime(f[30][:8], "%Y%m%d").date()
            except ValueError:
                dt = None
        amount_wan = to_float(f[37])
        total_yi, float_yi = to_float(f[45]), to_float(f[44])
        out[code] = Quote(
            code=code,
            name=f[1],
            price=to_float(f[3]),
            prev_close=to_float(f[4]),
            open=to_float(f[5]),
            high=to_float(f[33]),
            low=to_float(f[34]),
            volume=to_float(f[36]),
            amount=amount_wan * 1e4 if amount_wan is not None else None,
            change_pct=to_float(f[32]),
            pe_ttm=to_float(f[39]),
            pb=to_float(f[46]),
            total_mv=total_yi * 1e8 if total_yi else None,
            float_mv=float_yi * 1e8 if float_yi else None,
            dt=dt,
            raw={"source": "qt.gtimg.cn"},
        )
    return out
