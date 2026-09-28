"""多策略的每日扫描与查询：对价值池股票逐个判定，结果写入 strategy_signal_daily。"""

import asyncio
import logging
from collections import Counter
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.mysql import insert

from app.db.models import KlineDailyRaw, RecommendItem, RecommendRun, StockBasic, StrategySignalDaily, StructureStateDaily
from app.db.session import session_scope
from app.services.jobs import JobContext
from app.services.value_data import load_series, pool_codes
from app.strategies.dianjin import VARIANTS, ZONE_NAMES, evaluate

logger = logging.getLogger(__name__)

STALE_DAYS = 10  # 最新日线早于扫描日这么多天（长期停牌）的不参与当天判定
KEEP_DAYS = 400
BACKTEST_STATUS = "尚未检验：回测检验在多策略第三次交付完成后显示在这里。"
CAVEAT = "判定只用当天已公告、已除权的数据；价值池只含当前在市股票，存在幸存者偏差。"


def strategy_list() -> list[dict]:
    with session_scope() as db:
        latest = dict(db.execute(select(StrategySignalDaily.strategy, func.max(StrategySignalDaily.trade_date)).group_by(StrategySignalDaily.strategy)).all())
    return [
        {"key": k, "name": p.name, "rules": p.rules, "latest_date": latest[k].isoformat() if latest.get(k) else None,
         "backtest": BACKTEST_STATUS, "caveat": CAVEAT}
        for k, p in VARIANTS.items()
    ]


def _scan_one(code: str, scan_date: date) -> list[dict]:
    s = load_series(code, end=scan_date)
    if s is None or not s.dates or (scan_date - s.dates[-1]).days > STALE_DAYS:
        return []
    rows = []
    for key, p in VARIANTS.items():
        ev = evaluate(s, p)
        rows.append({
            "strategy": key, "code": code, "trade_date": scan_date, "passed": ev.passed, "zone": ev.zone,
            "metrics": {**ev.metrics, "as_of": s.dates[-1].isoformat()},
            "criteria": [c.__dict__ for c in ev.criteria],
        })
    return rows


def _save(rows: list[dict], scan_date: date) -> None:
    with session_scope() as db:
        db.execute(delete(StrategySignalDaily).where(StrategySignalDaily.trade_date == scan_date))
        db.execute(delete(StrategySignalDaily).where(StrategySignalDaily.trade_date < scan_date - timedelta(days=KEEP_DAYS)))
        for i in range(0, len(rows), 500):
            db.execute(insert(StrategySignalDaily).values(rows[i : i + 500]))


async def scan_strategies(ctx: JobContext | None = None) -> dict:
    with session_scope() as db:
        scan_date = db.scalar(select(func.max(KlineDailyRaw.trade_date)))
    if scan_date is None:
        return {"skipped": "价值池还没有数据，请先运行「补齐价值数据」"}
    codes = pool_codes()
    if ctx:
        ctx.update(done=0, total=len(codes), message=f"策略扫描：价值池 {len(codes)} 只，数据日期 {scan_date}", force=True)
    rows: list[dict] = []
    for n, code in enumerate(codes, 1):
        try:
            rows += await asyncio.to_thread(_scan_one, code, scan_date)
        except Exception:  # noqa: BLE001
            logger.exception("策略判定失败 %s", code)
        if ctx and n % 20 == 0:
            ctx.update(done=n, message=f"策略扫描 {n}/{len(codes)}")
    await asyncio.to_thread(_save, rows, scan_date)
    counts = Counter((r["strategy"], r["zone"]) for r in rows if r["passed"])
    out = {"date": scan_date.isoformat(), "stocks": len({r["code"] for r in rows}),
           **{k: {z: counts[(k, z)] for z in ZONE_NAMES if counts[(k, z)]} for k in VARIANTS}}
    if ctx:
        ctx.update(message=f"策略扫描完成：{out}", force=True)
    return out


def screen(key: str, zone: str | None = None, passed_only: bool = True) -> dict:
    if key not in VARIANTS:
        raise KeyError(key)
    with session_scope() as db:
        d = db.scalar(select(func.max(StrategySignalDaily.trade_date)).where(StrategySignalDaily.strategy == key))
        if d is None:
            return {"date": None, "total": 0, "items": [], "counts": {}, "fail_counts": {}, "zone_names": ZONE_NAMES}
        rows = db.execute(select(StrategySignalDaily).where(StrategySignalDaily.strategy == key, StrategySignalDaily.trade_date == d)).scalars().all()
        basics = {b.code: b for b in db.execute(select(StockBasic).where(StockBasic.code.in_([r.code for r in rows]))).scalars()}
    counts = Counter(r.zone for r in rows if r.passed)
    fails = Counter(c["name"] for r in rows for c in (r.criteria or []) if not c.get("passed"))
    picked = [r for r in rows if (r.passed or not passed_only) and (zone is None or r.zone == zone)]
    order = {"buy": 0, "near_buy": 1, "middle": 2, "sell": 3, "insufficient": 4}
    picked.sort(key=lambda r: (order.get(r.zone, 9), (r.metrics or {}).get("ratio") or 9))
    items = [{
        "code": r.code, "name": basics[r.code].name if r.code in basics else r.code,
        "industry": basics[r.code].industry if r.code in basics else None,
        "passed": r.passed, "zone": r.zone, "zone_name": ZONE_NAMES.get(r.zone, r.zone), **(r.metrics or {}), "criteria": r.criteria,
    } for r in picked]
    return {"date": d.isoformat(), "total": len(rows), "passed": sum(counts.values()), "items": items,
            "counts": dict(counts), "fail_counts": dict(fails), "zone_names": ZONE_NAMES}


def stock_views(code: str) -> list[dict]:
    """个股页的多策略视角：点金术两个版本的实时判定，加上缠论结构状态和是否在推荐里。"""
    out = []
    s = load_series(code)
    for key, p in VARIANTS.items():
        if s is None or not s.dates:
            out.append({"key": key, "name": p.name, "available": False,
                        "summary": "不在价值池：2014 年以来股息率从未达到 3%，不可能通过股息率条件"})
            continue
        out.append({"key": key, "name": p.name, "available": True, **evaluate(s, p).to_dict()})
    with session_scope() as db:
        st = db.execute(select(StructureStateDaily).where(StructureStateDaily.code == code).order_by(StructureStateDaily.trade_date.desc()).limit(1)).scalars().first()
        run = db.execute(select(RecommendRun).where(RecommendRun.status == "success").order_by(RecommendRun.id.desc()).limit(1)).scalars().first()
        item = db.execute(select(RecommendItem).where(RecommendItem.run_id == run.id, RecommendItem.code == code)).scalars().first() if run else None
    from app.analysis.structure import STATE_NAMES

    chan = {"key": "chan", "name": "缠论结构", "available": st is not None,
            "zone_name": STATE_NAMES.get(st.state, st.state) if st else None,
            "trade_date": st.trade_date.isoformat() if st else None,
            "summary": (f"日线结构：{STATE_NAMES.get(st.state, st.state)}" if st else "还没有结构状态")
                       + (f"；在推荐的{'主候选' if (item.pool or 'main') == 'main' else '观察池'}里（第 {item.rank} 名）" if item else "；不在今天的推荐里")}
    return [chan, *out]
