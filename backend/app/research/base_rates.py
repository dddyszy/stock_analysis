"""结构基准率：逐日回放，每个采样日只用截至当天的数据判定结构状态，统计之后先触及哪条价位线及收益分布。

与结构报告共用 analysis/structure.py 的 classify()，所以报告里的「历史比例」统计的正是同一种结构。
相邻几天的结构高度重复，每 STEP 个交易日取一个样本点。
"""

import asyncio
import bisect
import logging
import random
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

from sqlalchemy import delete, insert, or_, select

from app.analysis.market_env import INDEX_REGIME_CODE, index_regime_map
from app.analysis.structure import classify, first_touch, outcome
from app.chan import analyze
from app.db.models import KlineDaily, StockBasic, StructureBaseRate
from app.db.session import session_scope
from app.services.chan_service import chan_config
from app.services.jobs import JobContext
from app.services.strategy_config import get_active_params
from app.services.sync import load_bars

logger = logging.getLogger(__name__)

HORIZONS = (5, 10, 20)
STEP = 5
WINDOW = 400
WARMUP = 250
LOOKBACK = 1200
MIN_CELL = 50  # 样本少于该值的格子在报告里显示「样本不足」
REGIMES = ("all", "up", "range", "down")
OUTCOMES = ("continue", "exception", "undecided", "break_up", "break_down")
WORKERS = 6


def _at(series: dict, dates: list, d):
    i = bisect.bisect_right(dates, d)
    return series[dates[i - 1]] if i else None


def _one(args: tuple) -> list[dict]:
    code, bars, params, bench, bench_dates, regimes = args
    cfg = chan_config(params)
    out = []
    horizon = max(HORIZONS)
    for t in range(WARMUP, len(bars) - horizon, STEP):
        try:
            st = classify(analyze(bars[max(0, t - WINDOW) : t + 1], "day", cfg))
        except Exception:  # noqa: BLE001
            logger.exception("结构判定失败 %s", code)
            continue
        if st.key == "insufficient":
            continue
        d0, c0 = bars[t].dt, bars[t].close
        fwd = [b.close for b in bars[t + 1 : t + 1 + horizon]]
        b0 = _at(bench, bench_dates, d0)
        # 市场状态取判定日之前最后一个交易日，与回测口径一致
        i = bisect.bisect_left(bench_dates, d0)
        rec = {"state": st.key, "regime": regimes.get(bench_dates[i - 1], "unknown") if i else "unknown", "h": {}}
        for h in HORIZONS:
            ret = fwd[h - 1] / c0 - 1
            b1 = _at(bench, bench_dates, bars[t + h].dt)
            rec["h"][h] = {"o": outcome(st, first_touch(st, fwd[:h])), "r": ret,
                           "x": ret - (b1 / b0 - 1) if b0 and b1 else None}
        out.append(rec)
    return out


def _aggregate(recs: list[dict]) -> list[dict]:
    rows = []
    for regime in REGIMES:
        subset = recs if regime == "all" else [r for r in recs if r["regime"] == regime]
        by_state: dict[str, list[dict]] = {}
        for r in subset:
            by_state.setdefault(r["state"], []).append(r)
        for state, rs in by_state.items():
            for h in HORIZONS:
                cells = [r["h"][h] for r in rs]
                rets = [c["r"] for c in cells]
                xs = [c["x"] for c in cells if c["x"] is not None]
                rows.append({
                    "state": state, "regime": regime, "horizon": h, "n": len(cells),
                    "outcomes": {o: round(sum(c["o"] == o for c in cells) / len(cells), 4) for o in OUTCOMES},
                    "mean_ret": round(statistics.mean(rets) * 100, 3), "median_ret": round(statistics.median(rets) * 100, 3),
                    "p_positive": round(sum(x > 0 for x in rets) / len(rets), 4),
                    "mean_excess": round(statistics.mean(xs) * 100, 3) if xs else None,
                })
    return rows


def _pick(sample: int, seed: int) -> list[str]:
    with session_scope() as db:
        pool = sorted(db.execute(select(StockBasic.code).where(
            StockBasic.is_index.is_(False), or_(StockBasic.active.is_(True), StockBasic.delisted_on.is_not(None)))).scalars())
    random.Random(seed).shuffle(pool)
    return pool[:sample]


async def run_base_rates(ctx: JobContext, sample_size: int = 1000, seed: int = 2026) -> dict:
    params = get_active_params()
    codes = _pick(sample_size, seed)
    bench_bars = load_bars(KlineDaily, INDEX_REGIME_CODE, LOOKBACK + 100)
    bench = {b.dt: b.close for b in bench_bars}
    bench_dates = sorted(bench)
    regimes = index_regime_map(bench_bars)
    ctx.update(done=0, total=len(codes), message=f"逐日回放 {len(codes)} 只股票的结构状态", force=True)

    def _run() -> list[dict]:
        tasks = []
        for code in codes:
            bars = load_bars(KlineDaily, code, LOOKBACK)
            if len(bars) >= WARMUP + max(HORIZONS) + STEP:
                tasks.append((code, bars, params, bench, bench_dates, regimes))
        recs: list[dict] = []
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(_one, t) for t in tasks]
            for i, f in enumerate(as_completed(futures)):
                recs.extend(f.result())
                ctx.update(done=i + 1, total=len(tasks), message=f"结构回放 {i + 1}/{len(tasks)}，累计 {len(recs)} 个样本点")
        return recs

    recs = await asyncio.to_thread(_run)
    rows = _aggregate(recs)
    meta = {"sample_stocks": len(codes), "samples": len(recs), "step": STEP, "window": WINDOW, "seed": seed,
            "from": bench_dates[WARMUP].isoformat() if len(bench_dates) > WARMUP else None,
            "to": bench_dates[-1].isoformat() if bench_dates else None}
    now = datetime.now()
    with session_scope() as db:
        db.execute(delete(StructureBaseRate))
        for i in range(0, len(rows), 500):
            db.execute(insert(StructureBaseRate).values([{**r, "computed_at": now, "meta": meta} for r in rows[i : i + 500]]))
    ctx.update(message=f"结构基准率完成：{len(recs)} 个样本点，{len(rows)} 个统计格", force=True)
    return {"samples": len(recs), "cells": len(rows)}


def base_rate(state: str, regime: str, horizon: int = 10) -> dict | None:
    """报告用：优先取当前市场状态的统计，样本不足时退回全部样本。"""
    with session_scope() as db:
        for reg in (regime, "all"):
            row = db.get(StructureBaseRate, (state, reg, horizon))
            if row is not None and row.n >= MIN_CELL:
                return {"state": state, "regime": reg, "horizon": horizon, "n": row.n, "outcomes": row.outcomes,
                        "mean_ret": row.mean_ret, "median_ret": row.median_ret, "p_positive": row.p_positive,
                        "mean_excess": row.mean_excess, "computed_at": row.computed_at.isoformat(), "meta": row.meta}
    return None


def base_rate_table() -> dict:
    with session_scope() as db:
        rows = db.execute(select(StructureBaseRate)).scalars().all()
        meta = rows[0].meta if rows else None
        computed = rows[0].computed_at.isoformat() if rows else None
        return {"meta": meta, "computed_at": computed, "rows": [
            {"state": r.state, "regime": r.regime, "horizon": r.horizon, "n": r.n, "outcomes": r.outcomes, "mean_ret": r.mean_ret,
             "median_ret": r.median_ret, "p_positive": r.p_positive, "mean_excess": r.mean_excess} for r in rows]}
