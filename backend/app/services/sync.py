"""全市场数据同步：股票池、日线回填（断点续传）、每日增量与除权检测、周线合成、基本面。"""

import asyncio
import logging
import time
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.mysql import insert

from app.core.config import get_settings
from app.db.models import (
    FundamentalQuarterly,
    KlineDaily,
    KlineWeekly,
    StockBasic,
    SyncProgress,
    ValuationSnapshot,
)
from app.db.session import session_scope
from app.mcp.errors import McpAuthError, McpBusinessError, McpRateLimited
from app.providers import create_provider
from app.providers.base import INDEX_CODES, Bar, DataProvider, FinanceRecord
from app.services.jobs import JobContext
from app.services.kline_utils import resample_weekly, week_key

logger = logging.getLogger(__name__)
settings = get_settings()

EXRIGHT_TOLERANCE = 0.002
FINANCE_BATCH = 5
SCORE_BATCH = 100


# ---------- 通用读写 ----------


def upsert_bars(model, code: str, bars: list[Bar]) -> int:
    if not bars:
        return 0
    rows = [
        {
            "code": code,
            "trade_date": b.dt,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "amount": b.amount,
        }
        for b in bars
    ]
    with session_scope() as db:
        for i in range(0, len(rows), 1000):
            stmt = insert(model).values(rows[i : i + 1000])
            stmt = stmt.on_duplicate_key_update(
                open=stmt.inserted.open,
                high=stmt.inserted.high,
                low=stmt.inserted.low,
                close=stmt.inserted.close,
                volume=stmt.inserted.volume,
                amount=stmt.inserted.amount,
            )
            db.execute(stmt)
    return len(rows)


def load_bars(model, code: str, limit: int | None = None, end: date | None = None) -> list[Bar]:
    with session_scope() as db:
        q = select(model).where(model.code == code)
        if end is not None:
            q = q.where(model.trade_date <= end)
        q = q.order_by(model.trade_date.desc())
        if limit:
            q = q.limit(limit)
        rows = db.execute(q).scalars().all()
    # 早期数据按单精度存储过，读取时去掉尾数误差
    return [
        Bar(dt=r.trade_date, open=round(r.open, 3), high=round(r.high, 3), low=round(r.low, 3), close=round(r.close, 3),
            volume=r.volume, amount=r.amount)
        for r in reversed(rows)
    ]


def rebuild_weekly(code: str, since: date | None = None) -> None:
    """用日线重建周线。since 为空时全量重建，否则只重建 since 所在周及之后。"""
    if since is not None:
        y, w = week_key(since)
        week_start = date.fromisocalendar(y, w, 1)
    else:
        week_start = None
    with session_scope() as db:
        q = select(KlineDaily).where(KlineDaily.code == code)
        if week_start:
            q = q.where(KlineDaily.trade_date >= week_start)
        rows = db.execute(q.order_by(KlineDaily.trade_date)).scalars().all()
        daily = [Bar(r.trade_date, r.open, r.high, r.low, r.close, r.volume, r.amount) for r in rows]
        if week_start:
            db.execute(delete(KlineWeekly).where(KlineWeekly.code == code, KlineWeekly.trade_date >= week_start))
        else:
            db.execute(delete(KlineWeekly).where(KlineWeekly.code == code))
    upsert_bars(KlineWeekly, code, resample_weekly(daily))


def _set_progress(task: str, code: str, status: str, last_date: date | None = None, error: str | None = None) -> None:
    with session_scope() as db:
        stmt = insert(SyncProgress).values(task=task, code=code, status=status, last_date=last_date, error=error)
        stmt = stmt.on_duplicate_key_update(
            status=stmt.inserted.status,
            last_date=func.coalesce(stmt.inserted.last_date, SyncProgress.last_date),
            error=stmt.inserted.error,
        )
        db.execute(stmt)


def active_codes(include_index: bool = True) -> list[str]:
    with session_scope() as db:
        q = select(StockBasic.code).where(StockBasic.active.is_(True))
        if not include_index:
            q = q.where(StockBasic.is_index.is_(False))
        return list(db.execute(q.order_by(StockBasic.code)).scalars().all())


def last_bar_dates() -> dict[str, date]:
    with session_scope() as db:
        rows = db.execute(select(KlineDaily.code, func.max(KlineDaily.trade_date)).group_by(KlineDaily.code)).all()
    return {c: d for c, d in rows}


# ---------- 交易日 ----------

_calendar_cache: dict[int, list[date]] = {}


async def trading_days_around(provider: DataProvider, today: date | None = None) -> list[date]:
    today = today or date.today()
    key = today.toordinal()
    if key not in _calendar_cache:
        _calendar_cache.clear()
        _calendar_cache[key] = await provider.trading_days(today - timedelta(days=30), today + timedelta(days=10))
    return _calendar_cache[key]


async def is_trading_day(provider: DataProvider, d: date | None = None) -> bool:
    d = d or date.today()
    days = await trading_days_around(provider, d)
    if not days:
        return d.weekday() < 5
    return d in days


async def previous_trading_day(provider: DataProvider, d: date | None = None) -> date:
    d = d or date.today()
    days = [x for x in await trading_days_around(provider, d) if x < d]
    if days:
        return days[-1]
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev


# ---------- 股票池 ----------


async def sync_stock_pool(ctx: JobContext | None = None) -> dict:
    async with create_provider() as provider:
        industries = await provider.list_industries()
        if ctx:
            ctx.update(done=0, total=len(industries) + 1, message="同步申万一级行业成份股", force=True)
        seen: dict[str, dict] = {}
        for ind in industries:
            try:
                members = await provider.sector_constituents(ind.code)
            except (McpBusinessError, McpRateLimited) as exc:
                logger.warning("行业 %s 成份股获取失败: %s", ind.name, exc)
                members = []
            allowed = tuple(settings.exchanges.split(","))
            for m in members:
                if not m.code.startswith(allowed):
                    continue
                seen[m.code] = {"code": m.code, "name": m.name, "industry": ind.name, "industry_code": ind.code}
            if ctx:
                ctx.step(f"{ind.name}: {len(members)} 只")
        try:
            st = await provider.st_codes()
        except (McpBusinessError, McpRateLimited):
            st = set()
    if not seen:
        raise RuntimeError("股票池为空，请检查 data_sector 返回")
    rows = []
    for code, r in seen.items():
        rows.append({**r, "exchange": code[:2], "is_st": code in st or "ST" in r["name"].upper(), "is_index": False, "active": True})
    for code, name in INDEX_CODES.items():
        rows.append(
            {"code": code, "name": name, "exchange": code[:2], "industry": None, "industry_code": None, "is_st": False, "is_index": True, "active": True}
        )
    with session_scope() as db:
        db.execute(StockBasic.__table__.update().values(active=False))
        for i in range(0, len(rows), 1000):
            stmt = insert(StockBasic).values(rows[i : i + 1000])
            stmt = stmt.on_duplicate_key_update(
                name=stmt.inserted.name,
                exchange=stmt.inserted.exchange,
                industry=func.coalesce(stmt.inserted.industry, StockBasic.industry),
                industry_code=func.coalesce(stmt.inserted.industry_code, StockBasic.industry_code),
                is_st=stmt.inserted.is_st,
                is_index=stmt.inserted.is_index,
                active=True,
            )
            db.execute(stmt)
    if ctx:
        ctx.update(done=len(industries) + 1, message=f"股票池 {len(seen)} 只，ST {len(st)} 只", force=True)
    return {"stocks": len(seen), "st": len(st), "industries": len(industries)}


# ---------- K 线回填 ----------


async def _backfill_one(provider: DataProvider, code: str, start: date, end: date, full: bool) -> int:
    bars = await provider.kline(code, "day", start=start, end=end, fq="" if code in INDEX_CODES else "qfq")
    if full:
        with session_scope() as db:
            db.execute(delete(KlineDaily).where(KlineDaily.code == code))
    n = upsert_bars(KlineDaily, code, bars)
    rebuild_weekly(code, since=None if full else start)
    if bars:
        _update_list_date_proxy(code, bars[0].dt, start)
    return n


def _update_list_date_proxy(code: str, first_bar: date, requested_start: date) -> None:
    """首根 K 线明显晚于请求起点，说明是回填窗口内上市的新股，用它近似上市日期。"""
    if first_bar <= requested_start + timedelta(days=20):
        return
    with session_scope() as db:
        sb = db.get(StockBasic, code)
        if sb and (sb.list_date is None or sb.list_date > first_bar):
            sb.list_date = first_bar


async def backfill_klines(ctx: JobContext, codes: list[str] | None = None, force_full: bool = False) -> dict:
    today = date.today()
    start_default = today - timedelta(days=365 * settings.backfill_years)
    codes = codes or active_codes()
    last_dates = last_bar_dates()
    async with create_provider() as provider:
        prev_day = await previous_trading_day(provider, today)
    todo = []
    for code in codes:
        last = last_dates.get(code)
        if force_full or last is None:
            todo.append((code, start_default, True))
        elif last < prev_day:
            # 已到上一交易日的不再回填，当天 K 线由每日增量用行情快照补上
            todo.append((code, last + timedelta(days=1), False))
    # 北交所只能走限频的 MCP，放到最后，避免占住所有工作协程
    todo.sort(key=lambda x: x[0].startswith("bj"))
    ctx.update(done=0, total=len(todo), message=f"待回填 {len(todo)} 只", force=True)
    stats = {"ok": 0, "failed": 0, "bars": 0}
    queue: asyncio.Queue = asyncio.Queue()
    for item in todo:
        queue.put_nowait(item)

    async with create_provider() as provider:

        async def worker() -> None:
            while True:
                try:
                    code, start, full = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    n = await _backfill_one(provider, code, start, today, full)
                    stats["ok"] += 1
                    stats["bars"] += n
                    _set_progress("kline", code, "done", last_date=today)
                except McpAuthError:
                    raise
                except Exception as exc:
                    stats["failed"] += 1
                    _set_progress("kline", code, "failed", error=str(exc)[:1000])
                    logger.warning("回填 %s 失败: %s", code, exc)
                ctx.step(f"{code} 完成，成功 {stats['ok']}，失败 {stats['failed']}")

        workers = 8 if settings.kline_source == "public" else settings.mcp_max_concurrency
        await asyncio.gather(*(worker() for _ in range(max(1, workers))))
    ctx.update(message=f"回填完成：成功 {stats['ok']}，失败 {stats['failed']}，写入 {stats['bars']} 根", force=True)
    return stats


# ---------- 每日增量 ----------


async def daily_update(ctx: JobContext) -> dict:
    today = date.today()
    async with create_provider() as provider:
        if not await is_trading_day(provider, today):
            ctx.update(message="非交易日，跳过", force=True)
            return {"skipped": "non-trading-day"}
        prev_day = await previous_trading_day(provider, today)
        codes = active_codes()
        last_dates = last_bar_dates()
        last_close = _last_closes(codes, before=today)
        batch = settings.quote_batch_size
        ctx.update(done=0, total=len(codes), message="批量获取当日行情", force=True)
        stats = {"updated": 0, "exright": 0, "gap": 0, "suspended": 0}
        refetch_full: list[str] = []
        refetch_gap: list[str] = []
        val_rows = []
        for i in range(0, len(codes), batch):
            chunk = codes[i : i + batch]
            try:
                quotes = await provider.quotes(chunk)
            except (McpBusinessError, McpRateLimited) as exc:
                logger.warning("行情批次失败: %s", exc)
                quotes = {}
            for code in chunk:
                q = quotes.get(code)
                if q is None or not q.price or not q.open:
                    stats["suspended"] += 1
                    continue
                if q.dt is not None and q.dt != today:
                    stats["suspended"] += 1
                    continue
                last = last_dates.get(code)
                if last is None:
                    refetch_full.append(code)
                    continue
                if last < prev_day:
                    refetch_gap.append(code)
                    continue
                prev_local = last_close.get(code)
                if (
                    code not in INDEX_CODES
                    and q.prev_close
                    and prev_local
                    and abs(q.prev_close - prev_local) / prev_local > EXRIGHT_TOLERANCE
                ):
                    refetch_full.append(code)
                    continue
                bar = Bar(
                    dt=today,
                    open=q.open,
                    high=max(q.high or q.price, q.price),
                    low=min(q.low or q.price, q.price),
                    close=q.price,
                    volume=q.volume,
                    amount=q.amount,
                )
                upsert_bars(KlineDaily, code, [bar])
                rebuild_weekly(code, since=today)
                stats["updated"] += 1
                if code not in INDEX_CODES:
                    val_rows.append(
                        {"code": code, "trade_date": today, "pe_ttm": q.pe_ttm, "pb": q.pb, "total_mv": q.total_mv, "float_mv": q.float_mv}
                    )
            ctx.update(done=min(i + batch, len(codes)), message=f"已处理 {min(i + batch, len(codes))}/{len(codes)}")
        _upsert_valuations(val_rows)

        stats["exright"] = len(refetch_full)
        stats["gap"] = len(refetch_gap)
        start_default = today - timedelta(days=365 * settings.backfill_years)
        redo = [(c, start_default, True) for c in refetch_full] + [
            (c, last_dates[c] + timedelta(days=1), False) for c in refetch_gap
        ]
        ctx.update(message=f"重拉 {len(redo)} 只（除权 {len(refetch_full)}，缺口 {len(refetch_gap)}）", force=True)
        for code, start, full in redo:
            try:
                await _backfill_one(provider, code, start, today, full)
                _set_progress("kline", code, "done", last_date=today)
            except McpAuthError:
                raise
            except Exception as exc:
                _set_progress("kline", code, "failed", error=str(exc)[:1000])
    ctx.update(message=f"每日更新完成：{stats}", force=True)
    return stats


def _last_closes(codes: list[str], before: date) -> dict[str, float]:
    with session_scope() as db:
        sub = (
            select(KlineDaily.code, func.max(KlineDaily.trade_date).label("d"))
            .where(KlineDaily.trade_date < before)
            .group_by(KlineDaily.code)
            .subquery()
        )
        rows = db.execute(
            select(KlineDaily.code, KlineDaily.close).join(
                sub, (KlineDaily.code == sub.c.code) & (KlineDaily.trade_date == sub.c.d)
            )
        ).all()
    return {c: v for c, v in rows}


def _upsert_valuations(rows: list[dict]) -> None:
    if not rows:
        return
    with session_scope() as db:
        for i in range(0, len(rows), 1000):
            stmt = insert(ValuationSnapshot).values(rows[i : i + 1000])
            stmt = stmt.on_duplicate_key_update(
                pe_ttm=stmt.inserted.pe_ttm, pb=stmt.inserted.pb, total_mv=stmt.inserted.total_mv, float_mv=stmt.inserted.float_mv
            )
            db.execute(stmt)


# ---------- 基本面 ----------


def _derive(records: list[FinanceRecord]) -> list[dict]:
    by_date = {r.report_date: r for r in records}
    out = []
    for r in records:
        same_q_last_year = by_date.get(date(r.report_date.year - 1, r.report_date.month, r.report_date.day))

        def yoy(cur, prev):
            if cur is None or prev in (None, 0):
                return None
            return (cur - prev) / abs(prev) * 100

        months = r.report_date.month
        roe = r.roe
        if roe is None and r.net_profit is not None and r.total_equity:
            roe = r.net_profit / r.total_equity * 100 * (12 / months)
        gross = r.gross_margin
        if gross is None and r.revenue and r.operating_cost is not None:
            gross = (r.revenue - r.operating_cost) / r.revenue * 100
        debt = r.debt_ratio
        if debt is None and r.total_assets and r.total_liabilities is not None:
            debt = r.total_liabilities / r.total_assets * 100
        rev_yoy = r.revenue_yoy
        if rev_yoy is None:
            rev_yoy = yoy(r.revenue, same_q_last_year.revenue if same_q_last_year else None)
        profit_yoy = r.profit_yoy
        if profit_yoy is None:
            profit_yoy = yoy(r.net_profit, same_q_last_year.net_profit if same_q_last_year else None)
        out.append(
            {
                "report_date": r.report_date,
                "revenue": r.revenue,
                "net_profit": r.net_profit,
                "revenue_yoy": rev_yoy,
                "profit_yoy": profit_yoy,
                "gross_margin": gross,
                "roe": roe,
                "debt_ratio": debt,
                "op_cashflow": r.op_cashflow,
                "goodwill": r.goodwill,
                "total_equity": r.total_equity,
                "total_assets": r.total_assets,
            }
        )
    return out


async def sync_scores_and_valuations(ctx: JobContext | None = None, codes: list[str] | None = None) -> dict:
    """全市场层：腾讯诊股评分（每次 100 只）+ 行情快照里的 PE、PB、市值。调用量小，每天可以跑。"""
    codes = codes or active_codes(include_index=False)
    today = date.today()
    rows: dict[str, dict] = {}
    async with create_provider() as provider:
        if ctx:
            ctx.update(done=0, total=len(codes), message="同步诊股评分", force=True)
        for i in range(0, len(codes), SCORE_BATCH):
            chunk = codes[i : i + SCORE_BATCH]
            try:
                scores = await provider.scores(chunk)
            except (McpBusinessError, McpRateLimited) as exc:
                logger.warning("诊股评分批次失败: %s", exc)
                continue
            for c, v in scores.items():
                rows.setdefault(c, {"code": c, "trade_date": today}).update(
                    comp_score=v.get("comp"), funm_score=v.get("funm"), risk_score=v.get("risk")
                )
            if ctx:
                ctx.update(done=min(i + SCORE_BATCH, len(codes)), message=f"诊股评分 {min(i + SCORE_BATCH, len(codes))}/{len(codes)}")
        if ctx:
            ctx.update(message="同步估值（PE、PB、市值）", force=True)
        for i in range(0, len(codes), settings.quote_batch_size):
            chunk = codes[i : i + settings.quote_batch_size]
            try:
                quotes = await provider.quotes(chunk)
            except Exception as exc:
                logger.warning("估值批次失败: %s", exc)
                continue
            for c, q in quotes.items():
                rows.setdefault(c, {"code": c, "trade_date": today}).update(
                    pe_ttm=q.pe_ttm, pb=q.pb, total_mv=q.total_mv, float_mv=q.float_mv
                )
    cols = ("comp_score", "funm_score", "risk_score", "pe_ttm", "pb", "total_mv", "float_mv")
    values = [{"code": r["code"], "trade_date": today, **{k: r.get(k) for k in cols}} for r in rows.values()]
    with session_scope() as db:
        for i in range(0, len(values), 1000):
            stmt = insert(ValuationSnapshot).values(values[i : i + 1000])
            stmt = stmt.on_duplicate_key_update(
                **{k: func.coalesce(stmt.inserted[k], getattr(ValuationSnapshot, k)) for k in cols}
            )
            db.execute(stmt)
    return {"scored": sum(1 for r in values if r["comp_score"] is not None), "valued": sum(1 for r in values if r["pe_ttm"] is not None)}


def _fresh_fundamental_codes(codes: list[str], max_age_days: int) -> set[str]:
    since = datetime.now() - timedelta(days=max_age_days)
    with session_scope() as db:
        rows = db.execute(
            select(SyncProgress.code).where(
                SyncProgress.task == "fundamental", SyncProgress.status == "done",
                SyncProgress.updated_at >= since, SyncProgress.code.in_(codes),
            )
        ).scalars().all()
    return set(rows)


async def sync_finance_details(codes: list[str], ctx: JobContext | None = None, max_age_days: int = 20,
                               time_budget: float | None = None) -> dict:
    """候选股层：三大报表。westock 的 data_finance 单次最多可靠返回 5 只且限频严格，所以只拉候选股并缓存。

    codes 按优先级排序；给了 time_budget（秒）时，超时后不再开始新的批次，剩下的留给下次。"""
    deadline = time.monotonic() + time_budget if time_budget else None
    codes = [c for c in dict.fromkeys(codes) if not c.startswith(("sh000", "sz399"))]
    fresh = _fresh_fundamental_codes(codes, max_age_days)
    todo = [c for c in codes if c not in fresh]
    stats = {"ok": 0, "failed": 0, "cached": len(fresh)}
    if ctx:
        ctx.update(done=0, total=len(todo), message=f"拉取 {len(todo)} 只候选股的财报（{len(fresh)} 只已有缓存）", force=True)
    if not todo:
        return stats
    queue: asyncio.Queue = asyncio.Queue()
    for i in range(0, len(todo), FINANCE_BATCH):
        queue.put_nowait(todo[i : i + FINANCE_BATCH])
    async with create_provider() as provider:

        async def worker() -> None:
            while True:
                if deadline is not None and time.monotonic() > deadline:
                    stats["skipped"] = queue.qsize() * FINANCE_BATCH
                    return
                try:
                    chunk = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    call = provider.finance_batch(chunk, num=8)
                    if deadline is not None:
                        batch = await asyncio.wait_for(call, timeout=max(30.0, deadline - time.monotonic() + 60))
                    else:
                        batch = await call
                except McpAuthError:
                    raise
                except asyncio.TimeoutError:
                    stats["skipped"] = stats.get("skipped", 0) + len(chunk)
                    return
                except Exception as exc:
                    batch = {}
                    logger.warning("财报批次失败: %s", exc)
                for code in chunk:
                    rows = [{"code": code, **r} for r in _derive(batch.get(code, []))]
                    if not rows:
                        stats["failed"] += 1
                        _set_progress("fundamental", code, "failed", error="无财报数据或批次失败")
                        continue
                    with session_scope() as db:
                        stmt = insert(FundamentalQuarterly).values(rows)
                        stmt = stmt.on_duplicate_key_update(
                            **{k: stmt.inserted[k] for k in rows[0] if k not in ("code", "report_date")}
                        )
                        db.execute(stmt)
                    stats["ok"] += 1
                    _set_progress("fundamental", code, "done", last_date=date.today())
                if ctx:
                    ctx.update(done=stats["ok"] + stats["failed"], message=f"候选股财报：成功 {stats['ok']}，失败 {stats['failed']}")

        await asyncio.gather(*(worker() for _ in range(2)))
    return stats


def default_detail_codes() -> list[str]:
    """需要精细财报的股票：持仓、自选、最近一次推荐。"""
    from app.db.models import PositionPlan, RecommendItem, RecommendRun, Watchlist

    with session_scope() as db:
        codes = list(db.execute(select(PositionPlan.code).where(PositionPlan.status != "Closed")).scalars())
        codes += list(db.execute(select(Watchlist.code)).scalars())
        run_id = db.scalar(select(func.max(RecommendRun.id)).where(RecommendRun.status == "success"))
        if run_id:
            codes += list(db.execute(select(RecommendItem.code).where(RecommendItem.run_id == run_id)).scalars())
    return list(dict.fromkeys(codes))


async def sync_fundamentals(ctx: JobContext, codes: list[str] | None = None) -> dict:
    """基本面同步：全市场评分与估值 + 候选股（持仓、自选、最近推荐）的三大报表。"""
    result = {"market": await sync_scores_and_valuations(ctx, codes)}
    detail = codes if codes else default_detail_codes()
    result["details"] = await sync_finance_details(detail, ctx)
    ctx.update(message=f"基本面同步完成：{result}", force=True)
    return result


# 腾讯标签选股里与风险相关的标签：hard 硬过滤，penalty 扣分
RISK_LABELS: dict[str, tuple[str, str]] = {
    "risk_st": ("ST 与 *ST", "hard"),
    "price_below1": ("股价低于 1 元（面值退市风险）", "hard"),
    "marketcap_below10": ("市值 10 亿以下", "hard"),
    "fin_forecast_dec": ("业绩预亏预降", "penalty"),
    "fin_cash_neg_cfo": ("经营现金流为负", "penalty"),
    "fin_asset_high_bubble1": ("泡沫资产多", "penalty"),
    "fin_asset_high_bubble2": ("泡沫资产多", "penalty"),
}

# 腾讯事件驱动选股（tool_event）里与风险相关的事件；股权质押目前没有对应事件
RISK_EVENTS: dict[str, tuple[str, str]] = {
    "suspension_now": ("停牌中", "hard"),
    "deregulation_incoming": ("重大处罚将生效", "hard"),
    "deregulation_past_30": ("重大处罚生效一月内", "penalty"),
    "shareunlock_next_15": ("两周内限售解禁", "penalty"),
    "manager_shareplan_sell_implementing": ("董监高减持实施中", "penalty"),
    "manager_shareplan_sell_incoming": ("董监高计划减持", "penalty"),
    "suitcase": ("诉讼后一月内", "penalty"),
}


async def sync_risk_labels(ctx: JobContext | None = None) -> dict:
    """按标签批量拉取全市场风险名单（每个标签几次分页调用），替代逐只调用 data_risk。"""
    from app.db.models import StockRiskLabel

    today = date.today()
    counts: dict[str, int] = {}
    async with create_provider() as provider:
        items = [(k, v, "label") for k, v in RISK_LABELS.items()] + [(k, v, "event") for k, v in RISK_EVENTS.items()]
        if ctx:
            ctx.update(done=0, total=len(items), message="同步风险标签与风险事件", force=True)
        for i, (key, (name, severity), kind) in enumerate(items):
            label = key if kind == "label" else f"event:{key}"
            try:
                codes = await (provider.label_codes(key) if kind == "label" else provider.event_codes(key))
            except Exception as exc:
                logger.warning("风险标签 %s 获取失败，保留旧数据: %s", label, exc)
                continue
            with session_scope() as db:
                db.execute(delete(StockRiskLabel).where(StockRiskLabel.label == label))
                rows = [{"code": c, "label": label, "name": name, "severity": severity, "snap_date": today} for c in codes]
                for j in range(0, len(rows), 1000):
                    db.execute(insert(StockRiskLabel).values(rows[j : j + 1000]))
            counts[label] = len(codes)
            if ctx:
                ctx.update(done=i + 1, message=f"{name}：{len(codes)} 只")
    return counts


async def full_initialize(ctx: JobContext) -> dict:
    """首次初始化：股票池 → 日线回填 → 基本面。"""
    result = {"pool": await sync_stock_pool(ctx)}
    result["kline"] = await backfill_klines(ctx)
    result["fundamental"] = await sync_fundamentals(ctx)
    return result


def sync_overview() -> dict:
    with session_scope() as db:
        stocks = db.scalar(select(func.count()).select_from(StockBasic).where(StockBasic.active.is_(True))) or 0
        with_kline = db.scalar(select(func.count(func.distinct(KlineDaily.code)))) or 0
        max_date = db.scalar(select(func.max(KlineDaily.trade_date)))
        failed = db.scalar(select(func.count()).select_from(SyncProgress).where(SyncProgress.status == "failed")) or 0
        fund = db.scalar(select(func.count(func.distinct(FundamentalQuarterly.code)))) or 0
    return {
        "active_stocks": stocks,
        "stocks_with_kline": with_kline,
        "latest_kline_date": max_date.isoformat() if max_date else None,
        "failed_items": failed,
        "stocks_with_fundamental": fund,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
