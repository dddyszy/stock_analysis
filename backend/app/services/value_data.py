"""价值池数据：不复权日线与分红（腾讯公开接口）、带公告日的财报历史（MCP data_finance）、
历史条件选股快照（MCP tool_filter，只用来核对自算指标）。

价值池 = 2014 年以来股息率曾经达到 3% 的沪深股票；从未达到的股票任何一天都不可能通过点金术的股息率条件，
所以只给价值池保存不复权日线、拉财报，不会让回测结果偏乐观。
"""

import asyncio
import logging
import time
from datetime import date, datetime, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.mysql import insert

from app.analysis.value import Event, FinRow, build_series, max_dividend_yield, parse_fh, ValueSeries
from app.core.config import get_settings
from app.db.models import DividendEvent, FilterSnapshot, FinanceHistory, KlineDailyRaw, StockBasic, SyncProgress, ValuationSnapshot
from app.db.session import session_scope
from app.mcp.errors import McpAuthError, McpBusinessError, McpRateLimited
from app.providers import create_provider
from app.providers.parsing import to_date, to_float
from app.providers.tencent_public import TencentPublicQuotes
from app.services.jobs import JobContext
from app.services.runtime_state import get_state, set_state
from app.services.sync import _set_progress, active_codes, is_trading_day, upsert_bars

logger = logging.getLogger(__name__)
settings = get_settings()

VALUE_START = date(2014, 1, 1)
FIN_START = "2011-12-31"
POOL_MIN_YIELD = 3.0
FIN_BATCH = 5
FIN_MAX_AGE_DAYS = 30
DAILY_REFRESH_DAYS = 30
KLINE_WORKERS = 6
FILTER_START = date(2018, 1, 1)
VALIDATION_KEY = "value_validation"

# 核对用的条件选股表达式：点金术改良版的前三条（PE、股息率、市值）
FILTER_EXPRESSIONS = {
    "djs_pe20_dy4_mv50": "intersect([PE_TTM > 0, PE_TTM < 20, DividendRatioTTM > 4, TotalMV > 5000000000])",
}


def _is_mock() -> bool:
    return settings.data_provider == "mock"


# ---------- 读写 ----------


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


def _upsert_events(rows: list[dict]) -> None:
    if not rows:
        return
    with session_scope() as db:
        stmt = insert(DividendEvent).values(rows)
        stmt = stmt.on_duplicate_key_update(**{k: stmt.inserted[k] for k in ("record_date", "year", "cash", "bonus", "transfer", "content")})
        db.execute(stmt)


def finance_rows(code: str, raw: list[dict]) -> list[dict]:
    rows = []
    for r in raw:
        rd = to_date(r.get("EndDate") or r.get("date"))
        if rd is None:
            continue
        publ = to_date(str(r.get("InfoPublDate") or "")[:10])
        rows.append({
            "code": code, "report_date": rd, "publ_date": publ,
            "roe": to_float(r.get("ROE")), "roe_weighted": to_float(r.get("ROEWeighted")),
            "eps_ttm": to_float(r.get("EPSTTM")), "np_ttm": to_float(r.get("NPParentCompanyOwnersTTM")),
            "revenue_ttm": to_float(r.get("OperatingRevenueTTM")), "dividend_ttm": to_float(r.get("DividendTTM")),
        })
    return rows


def _upsert_finance(rows: list[dict]) -> None:
    if not rows:
        return
    cols = ("publ_date", "roe", "roe_weighted", "eps_ttm", "np_ttm", "revenue_ttm", "dividend_ttm")
    with session_scope() as db:
        stmt = insert(FinanceHistory).values(rows)
        stmt = stmt.on_duplicate_key_update(**{k: stmt.inserted[k] for k in cols})
        db.execute(stmt)


def pool_codes() -> list[str]:
    with session_scope() as db:
        return list(db.execute(select(distinct(KlineDailyRaw.code)).order_by(KlineDailyRaw.code)).scalars().all())


def _events_of(db, code: str) -> list[Event]:
    rows = db.execute(select(DividendEvent).where(DividendEvent.code == code)).scalars().all()
    return [Event(r.ex_date, r.cash or 0.0, r.bonus or 0.0, r.transfer or 0.0) for r in rows]


def load_series(code: str, end: date | None = None) -> ValueSeries | None:
    with session_scope() as db:
        q = select(KlineDailyRaw.trade_date, KlineDailyRaw.open, KlineDailyRaw.close).where(KlineDailyRaw.code == code)
        if end:
            q = q.where(KlineDailyRaw.trade_date <= end)
        bars = [(d, o, c) for d, o, c in db.execute(q.order_by(KlineDailyRaw.trade_date)).all()]
        if not bars:
            return None
        events = _events_of(db, code)
        fin = [
            FinRow(r.report_date, r.publ_date, r.roe, r.roe_weighted, r.eps_ttm, r.np_ttm)
            for r in db.execute(select(FinanceHistory).where(FinanceHistory.code == code)).scalars()
        ]
    return build_series(code, bars, events, fin)


# ---------- 不复权日线与分红 ----------


async def _fetch_raw(pub: TencentPublicQuotes, code: str, start: date, keep: bool) -> bool:
    """拉不复权日线和除权事件；股息率曾达到 3%（或 keep）时保存日线。返回是否属于价值池。"""
    bars, raw_events = await pub.kline_raw(code, start)
    return await asyncio.to_thread(_store_raw, code, bars, raw_events, keep)


def _store_raw(code: str, bars: list, raw_events: list[dict], keep: bool) -> bool:
    rows = event_rows(code, raw_events)
    _upsert_events(rows)
    if not bars:
        return False
    events = [Event(r["ex_date"], r["cash"], r["bonus"], r["transfer"]) for r in rows]
    in_pool = keep or max_dividend_yield([(b.dt, b.open, b.close) for b in bars], events) >= POOL_MIN_YIELD
    if in_pool:
        upsert_bars(KlineDailyRaw, code, bars)
    return in_pool


def _done_codes(task: str, since: datetime | None = None) -> set[str]:
    with session_scope() as db:
        q = select(SyncProgress.code).where(SyncProgress.task == task, SyncProgress.status == "done")
        if since is not None:
            q = q.where(SyncProgress.updated_at >= since)
        return set(db.execute(q).scalars().all())


async def sync_value_klines(ctx: JobContext | None = None, codes: list[str] | None = None, force: bool = False) -> dict:
    """全部沪深股票拉一遍 2014 年以来的不复权日线与分红，断点续传。"""
    if _is_mock():
        return {"skipped": "mock"}
    codes = codes or [c for c in active_codes(include_index=False) if c[:2] in ("sh", "sz")]
    done = set() if force else _done_codes("value_kline")
    todo = [c for c in codes if c not in done]
    pool = set(pool_codes())
    stats = {"total": len(todo), "pool": 0, "skipped": len(codes) - len(todo), "failed": 0}
    if ctx:
        ctx.update(done=0, total=len(todo), message=f"拉取不复权日线与分红：待处理 {len(todo)} 只（已完成 {stats['skipped']} 只）", force=True)
    queue: asyncio.Queue = asyncio.Queue()
    for c in todo:
        queue.put_nowait(c)
    pub = TencentPublicQuotes()
    finished = 0

    async def worker() -> None:
        nonlocal finished
        while True:
            try:
                code = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                in_pool = await _fetch_raw(pub, code, VALUE_START, keep=code in pool)
                stats["pool"] += int(in_pool)
                await asyncio.to_thread(_set_progress, "value_kline", code, "done", date.today())
            except Exception as exc:
                stats["failed"] += 1
                await asyncio.to_thread(_set_progress, "value_kline", code, "failed", None, str(exc)[:1000])
            finished += 1
            if ctx:
                ctx.update(done=finished, message=f"不复权日线与分红 {finished}/{len(todo)}，价值池 {stats['pool']} 只，失败 {stats['failed']}")

    try:
        await asyncio.gather(*(worker() for _ in range(KLINE_WORKERS)))
    finally:
        await pub.close()
    return stats


# ---------- 财报历史 ----------


async def sync_value_finance(ctx: JobContext | None = None, codes: list[str] | None = None,
                             max_age_days: int = FIN_MAX_AGE_DAYS, time_budget: float | None = None) -> dict:
    """给价值池拉 2011 年以来的利润表指标（带公告日）；每批 5 只一次调用，受 MCP 限频约束，断点续传。"""
    if _is_mock():
        return {"skipped": "mock"}
    codes = codes or pool_codes()
    fresh = _done_codes("value_finance", datetime.now() - timedelta(days=max_age_days))
    todo = [c for c in codes if c not in fresh]
    stats = {"total": len(todo), "ok": 0, "empty": 0, "failed_batches": 0, "cached": len(codes) - len(todo)}
    if ctx:
        ctx.update(done=0, total=len(todo), message=f"拉取价值池财报历史：待处理 {len(todo)} 只（{stats['cached']} 只已是最新）", force=True)
    deadline = time.monotonic() + time_budget if time_budget else None
    end = date.today().isoformat()
    async with create_provider() as provider:
        for i in range(0, len(todo), FIN_BATCH):
            if deadline is not None and time.monotonic() > deadline:
                stats["deferred"] = len(todo) - i
                break
            chunk = todo[i : i + FIN_BATCH]
            try:
                batch = await provider.finance_history(chunk, FIN_START, end)
            except McpAuthError:
                raise
            except (McpBusinessError, McpRateLimited) as exc:
                stats["failed_batches"] += 1
                logger.warning("财报历史批次失败: %s", exc)
                continue
            for code in chunk:
                rows = finance_rows(code, batch.get(code, []))
                if rows:
                    _upsert_finance(rows)
                    stats["ok"] += 1
                else:
                    stats["empty"] += 1
                _set_progress("value_finance", code, "done", last_date=date.today())
            if ctx:
                ctx.update(done=min(i + FIN_BATCH, len(todo)), message=f"财报历史 {min(i + FIN_BATCH, len(todo))}/{len(todo)}，成功 {stats['ok']}，无数据 {stats['empty']}")
    return stats


# ---------- 条件选股快照与核对 ----------


def _snapshot_dates() -> list[date]:
    """2018 年以来每个季度最后一个交易日（用价值池里交易日最全的日期序列近似）。"""
    with session_scope() as db:
        days = db.execute(
            select(KlineDailyRaw.trade_date).where(KlineDailyRaw.trade_date >= FILTER_START)
            .group_by(KlineDailyRaw.trade_date).having(func.count() > 50).order_by(KlineDailyRaw.trade_date)
        ).scalars().all()
    out: dict[tuple[int, int], date] = {}
    for d in days:
        if d.month in (3, 6, 9, 12):
            out[(d.year, d.month)] = d
    today = date.today()
    return [d for (y, m), d in sorted(out.items()) if (y, m) != (today.year, today.month)]


async def sync_filter_snapshots(ctx: JobContext | None = None) -> dict:
    if _is_mock():
        return {"skipped": "mock"}
    dates = _snapshot_dates()
    with session_scope() as db:
        have = {(k, d) for k, d in db.execute(select(FilterSnapshot.key, FilterSnapshot.snap_date)).all()}
    todo = [(k, d) for d in dates for k in FILTER_EXPRESSIONS if (k, d) not in have]
    stats = {"total": len(todo), "ok": 0, "failed": 0}
    if ctx:
        ctx.update(done=0, total=len(todo), message=f"拉取历史条件选股快照 {len(todo)} 个", force=True)
    async with create_provider() as provider:
        for n, (key, d) in enumerate(todo, 1):
            try:
                total, codes = await provider.filter_codes(FILTER_EXPRESSIONS[key], d)
            except McpAuthError:
                raise
            except (McpBusinessError, McpRateLimited) as exc:
                stats["failed"] += 1
                logger.info("条件选股快照 %s %s 失败: %s", key, d, exc)
                continue
            with session_scope() as db:
                db.merge(FilterSnapshot(key=key, snap_date=d, expression=FILTER_EXPRESSIONS[key], total=total, codes=codes))
            stats["ok"] += 1
            if ctx:
                ctx.update(done=n, message=f"条件选股快照 {n}/{len(todo)}，成功 {stats['ok']}，失败 {stats['failed']}")
    return stats


def passes_basic(s: ValueSeries, i: int, max_pe: float = 20, min_dy: float = 4, min_mv: float = 5e9) -> bool:
    pe, dy, mv = s.pe[i], s.dy[i], s.mv[i]
    return pe is not None and 0 < pe < max_pe and dy is not None and dy > min_dy and mv is not None and mv > min_mv


def validate_against_filters(ctx: JobContext | None = None) -> dict:
    """在每个快照日，用自算的 PE、股息率、市值重建同一条件，和腾讯条件选股的结果比较。"""
    with session_scope() as db:
        snaps = db.execute(select(FilterSnapshot).where(FilterSnapshot.key == "djs_pe20_dy4_mv50").order_by(FilterSnapshot.snap_date)).scalars().all()
        snaps = [(s.snap_date, set(s.codes or [])) for s in snaps]
        listed = set(db.execute(select(StockBasic.code)).scalars().all())
    if not snaps:
        return {"dates": 0}
    codes = pool_codes()
    mine: dict[date, set[str]] = {d: set() for d, _ in snaps}
    for n, code in enumerate(codes, 1):
        s = load_series(code)
        if s is None:
            continue
        for d, _ in snaps:
            i = s.index_on(d)
            if i is not None and (d - s.dates[i]).days <= 10 and passes_basic(s, i):
                mine[d].add(code)
        if ctx and n % 50 == 0:
            ctx.update(message=f"核对自算指标 {n}/{len(codes)}")
    rows = []
    for d, theirs_all in snaps:
        theirs = {c for c in theirs_all if c[:2] in ("sh", "sz")}
        gone = {c for c in theirs if c not in listed}
        comparable = theirs - gone
        both = mine[d] & comparable
        rows.append({
            "date": d.isoformat(), "theirs": len(comparable), "mine": len(mine[d]), "both": len(both),
            "precision": round(len(both) / len(mine[d]), 4) if mine[d] else None,
            "recall": round(len(both) / len(comparable), 4) if comparable else None,
            "delisted": len(gone),
        })
    valid = [r for r in rows if r["precision"] is not None and r["recall"] is not None]
    summary = {
        "dates": len(rows),
        "precision": round(sum(r["precision"] for r in valid) / len(valid), 4) if valid else None,
        "recall": round(sum(r["recall"] for r in valid) / len(valid), 4) if valid else None,
        "delisted": sum(r["delisted"] for r in rows),
        "rows": rows,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }
    set_state(VALIDATION_KEY, summary)
    return {k: v for k, v in summary.items() if k != "rows"}


# ---------- 任务入口 ----------


async def value_backfill(ctx: JobContext) -> dict:
    """首次补齐：不复权日线与分红 → 财报历史 → 历史条件选股快照 → 核对。"""
    if _is_mock():
        ctx.update(message="模拟数据模式不拉取价值数据", force=True)
        return {"skipped": "mock"}
    out = {"klines": await sync_value_klines(ctx)}
    out["finance"] = await sync_value_finance(ctx)
    out["filters"] = await sync_filter_snapshots(ctx)
    out["validation"] = validate_against_filters(ctx)
    ctx.update(message=f"价值数据补齐完成：价值池 {len(pool_codes())} 只，核对 {out['validation']}", force=True)
    return out


async def value_daily(ctx: JobContext) -> dict:
    """每日：刷新价值池近 30 天不复权日线（顺带发现新的除权），补进当天股息率达到 3% 的新股票，分批刷新旧财报。"""
    if _is_mock():
        return {"skipped": "mock"}
    today = date.today()
    async with create_provider() as provider:
        if not await is_trading_day(provider, today):
            ctx.update(message="非交易日，跳过", force=True)
            return {"skipped": "non-trading-day"}
    pool = set(pool_codes())
    with session_scope() as db:
        latest = db.scalar(select(func.max(ValuationSnapshot.trade_date)))
        newcomers = []
        if latest:
            newcomers = [
                c for c in db.execute(
                    select(ValuationSnapshot.code).where(ValuationSnapshot.trade_date == latest, ValuationSnapshot.dividend_yield >= POOL_MIN_YIELD)
                ).scalars() if c not in pool and c[:2] in ("sh", "sz")
            ]
    stats = {"refreshed": 0, "failed": 0, "newcomers": 0}
    ctx.update(done=0, total=len(pool) + len(newcomers), message=f"刷新价值池 {len(pool)} 只，新进 {len(newcomers)} 只", force=True)
    pub = TencentPublicQuotes()
    queue: asyncio.Queue = asyncio.Queue()
    for c in pool:
        queue.put_nowait((c, today - timedelta(days=DAILY_REFRESH_DAYS), True))
    for c in newcomers:
        queue.put_nowait((c, VALUE_START, False))
    finished = 0

    async def worker() -> None:
        nonlocal finished
        while True:
            try:
                code, start, keep = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                if keep:
                    bars, raw_events = await pub.kline_raw(code, start)
                    await asyncio.to_thread(_store_raw, code, bars, raw_events, True)
                    stats["refreshed"] += 1
                elif await _fetch_raw(pub, code, start, keep=False):
                    stats["newcomers"] += 1
                    _set_progress("value_kline", code, "done", last_date=today)
            except Exception as exc:
                stats["failed"] += 1
                logger.warning("价值池日线刷新失败 %s: %s", code, exc)
            finished += 1
            ctx.update(done=finished, message=f"价值池日线 {finished}/{len(pool) + len(newcomers)}")

    try:
        await asyncio.gather(*(worker() for _ in range(KLINE_WORKERS)))
    finally:
        await pub.close()
    stats["finance"] = await sync_value_finance(ctx, time_budget=600)
    return stats


def value_overview() -> dict:
    with session_scope() as db:
        pool = db.scalar(select(func.count(distinct(KlineDailyRaw.code)))) or 0
        bars = db.scalar(select(func.count()).select_from(KlineDailyRaw)) or 0
        first, last = db.execute(select(func.min(KlineDailyRaw.trade_date), func.max(KlineDailyRaw.trade_date))).one()
        scanned = db.scalar(select(func.count()).select_from(SyncProgress).where(SyncProgress.task == "value_kline", SyncProgress.status == "done")) or 0
        events = db.scalar(select(func.count()).select_from(DividendEvent)) or 0
        fin_codes = db.scalar(select(func.count(distinct(FinanceHistory.code)))) or 0
        fin_rows = db.scalar(select(func.count()).select_from(FinanceHistory)) or 0
        fin_first = db.scalar(select(func.min(FinanceHistory.report_date)))
        snaps = db.scalar(select(func.count()).select_from(FilterSnapshot)) or 0
    validation = get_state(VALIDATION_KEY) or {}
    return {
        "scanned": scanned, "pool": pool, "bars": bars, "first": first.isoformat() if first else None,
        "last": last.isoformat() if last else None, "events": events,
        "finance_codes": fin_codes, "finance_rows": fin_rows, "finance_first": fin_first.isoformat() if fin_first else None,
        "filter_snapshots": snaps,
        "validation": {k: v for k, v in validation.items() if k != "rows"},
        "validation_rows": validation.get("rows", []),
    }
