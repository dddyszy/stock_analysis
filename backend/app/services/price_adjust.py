"""日线复权：把腾讯前复权换算成等比前复权，并维护分红送转事件表（dividend_event）。

- 腾讯前复权对每次除权按 p' = (p − 派现) ÷ (1 + 送转) 变换；kline_daily 统一存等比前复权，最新一天等于原价。
- 配股等没有分红文字的除权，用除权前几天的前复权价和原价拟合出等效的（送转，派现），派现为负即配股缴款。
- 已存的旧日线（腾讯前复权口径）由 convert_stored_klines 一次性换算，按股票记进度（sync_progress 的 kline_ratio），不会重复换算。
"""

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import case, select
from sqlalchemy.dialects.mysql import insert

from app.analysis.adjust import to_ratio_adjusted, unadjust_maps
from app.analysis.value import Event, parse_fh
from app.db.models import DividendEvent
from app.db.session import session_scope
from app.providers.base import Bar
from app.providers.parsing import to_date
from app.providers.tencent_public import TencentPublicQuotes

logger = logging.getLogger(__name__)

FIT_BARS = 8
FIT_WINDOW_DAYS = 40


def event_rows(code: str, raw: list[dict]) -> list[dict]:
    rows = []
    for e in raw:
        ex = to_date(e.get("cqr"))
        if ex is None:
            continue
        cash, bonus, transfer = parse_fh(e.get("FHcontent"))
        rows.append({
            "code": code, "ex_date": ex, "record_date": to_date(e.get("djr")), "year": (e.get("nd") or None),
            "cash": cash, "bonus": bonus, "transfer": transfer, "content": (e.get("FHcontent") or None),
        })
    return rows


def upsert_events(rows: list[dict]) -> None:
    """没有分红文字的事件只插入不覆盖，保留已经拟合出的等效系数。"""
    if not rows:
        return
    with session_scope() as db:
        stmt = insert(DividendEvent).values(rows)
        stmt = stmt.on_duplicate_key_update(
            record_date=stmt.inserted.record_date, year=stmt.inserted.year, content=stmt.inserted.content,
            **{k: _keep_fitted(stmt, k) for k in ("cash", "bonus", "transfer")},
        )
        db.execute(stmt)


def _keep_fitted(stmt, col: str):
    return case((stmt.inserted.content.is_(None), getattr(DividendEvent, col)), else_=stmt.inserted[col])


def load_events(code: str) -> list[Event]:
    with session_scope() as db:
        rows = db.execute(select(DividendEvent).where(DividendEvent.code == code)).scalars().all()
    return [Event(r.ex_date, r.cash or 0.0, r.bonus or 0.0, r.transfer or 0.0) for r in rows]


def unfitted_dates(code: str) -> list[date]:
    """没有分红文字、也还没拟合出系数的除权日（多为配股）。"""
    with session_scope() as db:
        rows = db.execute(
            select(DividendEvent.ex_date).where(
                DividendEvent.code == code, DividendEvent.content.is_(None),
                DividendEvent.cash == 0, DividendEvent.bonus == 0, DividendEvent.transfer == 0,
            )
        ).scalars().all()
    return sorted(rows)


def fit_coefficients(qfq: list[Bar], raw: dict[date, float], later: list[Event]) -> tuple[float, float] | None:
    """用除权前的若干根 K 线拟合 原价 = s × y + c，其中 y 是撤销之后各次除权后的价格。"""
    maps = unadjust_maps([b.dt for b in qfq], later)
    pts = [(a * b.close + bb, raw[b.dt]) for b, (a, bb) in zip(qfq, maps) if b.dt in raw]
    pts = pts[-FIT_BARS:]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    var = sum((x - mx) ** 2 for x, _ in pts)
    if var <= 1e-12:
        return None
    s = sum((x - mx) * (y - my) for x, y in pts) / var
    c = my - s * mx
    worst = max(abs(s * x + c - y) / y for x, y in pts)
    if not (0.3 < s < 10) or worst > 0.005:
        return None
    return s, c


async def fit_unknown_events(pub: TencentPublicQuotes, code: str) -> int:
    """从最近一次开始拟合，每次拟合都要用到它之后各次除权的系数。"""
    fitted = 0
    for ex in sorted(await asyncio.to_thread(unfitted_dates, code), reverse=True):
        start, end = ex - timedelta(days=FIT_WINDOW_DAYS), ex - timedelta(days=1)
        try:
            qfq, _ = await pub.kline_events(code, start, end, "qfq")
            raw_bars, _ = await pub.kline_events(code, start, end, "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("拟合除权系数时拉取日线失败 %s %s: %s", code, ex, exc)
            continue
        later = [e for e in await asyncio.to_thread(load_events, code) if e.ex_date > ex]
        coef = fit_coefficients(qfq, {b.dt: b.close for b in raw_bars}, later)
        if coef is None:
            logger.info("除权系数拟合失败 %s %s", code, ex)
            continue
        s, c = coef
        with session_scope() as db:
            row = db.get(DividendEvent, (code, ex))
            if row is not None:
                row.bonus, row.transfer, row.cash = 0.0, s - 1, c
        fitted += 1
    return fitted


async def adjusted_daily(provider, code: str, start: date, end: date) -> list[Bar]:
    """拉腾讯前复权日线并换算成等比前复权；顺带把事件写进分红送转表。"""
    bars, raw_events = await provider.kline_with_events(code, start, end, "qfq")
    if not raw_events:
        return bars
    await asyncio.to_thread(upsert_events, event_rows(code, raw_events))
    if await asyncio.to_thread(unfitted_dates, code):
        pub = getattr(provider, "public", None) or TencentPublicQuotes()
        try:
            await fit_unknown_events(pub, code)
        finally:
            if pub is not getattr(provider, "public", None):
                await pub.close()
    events = await asyncio.to_thread(load_events, code)
    return to_ratio_adjusted(bars, events)


def max_abnormal_jump(bars: list[Bar], limit: float) -> int:
    """换算后单日涨跌幅超过涨跌停 3 个百分点以上的天数；大于 0 说明有没识别出的除权。"""
    return sum(1 for a, b in zip(bars, bars[1:]) if a.close and abs(b.close / a.close - 1) > limit + 0.03)
