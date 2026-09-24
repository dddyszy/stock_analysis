"""推荐跟踪：每条推荐之后 5/10/20 个交易日的收益、相对指数的超额、先触及止损还是目标一，以及每批推荐的 Rank IC。

- 入场价取推荐日次日开盘价；次日一字涨停视为买不进（blocked），不计入收益统计。
- 基准为沪深 300 和中证 1000，同样从次日开盘算到第 N 日收盘。
- 只读本地日线，没有外部调用，可以每天收盘后全量刷新最近 60 天的推荐。
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert

from app.db.models import KlineDaily, RecommendItem, RecommendPerf, RecommendRun, StockBasic
from app.db.session import session_scope
from app.providers.base import Bar
from app.services.ashare_rules import is_one_price_limit_up
from app.services.jobs import JobContext

logger = logging.getLogger(__name__)

HORIZONS = (5, 10, 20)
BENCHES = {"300": "sh000300", "1000": "sh000852"}
LOOKBACK_DAYS = 60


@dataclass
class ItemRef:
    code: str
    stop_price: float | None
    target1: float | None
    is_st: bool = False


def _bars_after(code: str, after: date, limit: int = 21) -> list[Bar]:
    with session_scope() as db:
        rows = db.execute(
            select(KlineDaily).where(KlineDaily.code == code, KlineDaily.trade_date > after)
            .order_by(KlineDaily.trade_date).limit(limit)
        ).scalars().all()
        prev = db.execute(
            select(KlineDaily.close).where(KlineDaily.code == code, KlineDaily.trade_date <= after)
            .order_by(KlineDaily.trade_date.desc()).limit(1)
        ).scalar()
    bars = [Bar(r.trade_date, r.open, r.high, r.low, r.close, r.volume) for r in rows]
    return [Bar(after, prev, prev, prev, prev)] + bars if prev is not None else bars


def compute_perf(item: ItemRef, bars: list[Bar], benches: dict[str, list[Bar]]) -> dict:
    """bars[0] 为推荐日（只用它的收盘价判断次日是否一字涨停），bars[1:] 为之后的交易日。"""
    out: dict = {"days": 0, "blocked": False, "entry_date": None, "entry_price": None, "max_drawdown": None, "first_hit": None}
    for h in HORIZONS:
        out[f"ret{h}"] = None
        for k in BENCHES:
            out[f"ex{k}_{h}"] = None
    if len(bars) < 2:
        return out
    base, after = bars[0], bars[1:]
    first = after[0]
    out["entry_date"] = first.dt
    if is_one_price_limit_up(first, base.close, item.code, item.is_st):
        out["blocked"] = True
        return out
    entry = first.open
    out["entry_price"] = entry
    window = after[: max(HORIZONS)]
    out["days"] = len(window)
    out["max_drawdown"] = (min(b.low for b in window) / entry - 1) * 100
    for b in window:
        hit_stop = item.stop_price is not None and b.low <= item.stop_price
        hit_target = item.target1 is not None and b.high >= item.target1
        if hit_stop:  # 同一天都触及时保守地记为先止损
            out["first_hit"] = "stop"
            break
        if hit_target:
            out["first_hit"] = "target"
            break
    bench_by_date = {k: {b.dt: b for b in v} for k, v in benches.items()}
    for h in HORIZONS:
        if len(window) < h:
            continue
        exit_bar = window[h - 1]
        ret = (exit_bar.close / entry - 1) * 100
        out[f"ret{h}"] = ret
        for k, series in bench_by_date.items():
            b0, b1 = series.get(first.dt), series.get(exit_bar.dt)
            if b0 and b1 and b0.open:
                out[f"ex{k}_{h}"] = ret - (b1.close / b0.open - 1) * 100
    return out


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 5 or len(x) != len(y):
        return None
    rx, ry = _rank(x), _rank(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy)


async def update_tracking(ctx: JobContext | None = None) -> dict:
    since = date.today() - timedelta(days=LOOKBACK_DAYS)
    with session_scope() as db:
        rows = db.execute(
            select(RecommendItem, RecommendRun.run_date, RecommendRun.market_regime)
            .join(RecommendRun, RecommendRun.id == RecommendItem.run_id)
            .where(RecommendRun.status == "success", RecommendRun.run_date >= since)
        ).all()
        st = set(db.execute(select(StockBasic.code).where(StockBasic.is_st.is_(True))).scalars())
        done = {pid for pid, days in db.execute(select(RecommendPerf.item_id, RecommendPerf.days)).all() if days >= max(HORIZONS)}
    todo = [(it, d, regime) for it, d, regime in rows if it.id not in done]
    if ctx:
        ctx.update(done=0, total=len(todo), message=f"更新 {len(todo)} 条推荐的后续表现", force=True)
    bench_cache: dict[date, dict[str, list[Bar]]] = {}
    values = []
    for i, (it, run_date, regime) in enumerate(todo):
        if run_date not in bench_cache:
            bench_cache[run_date] = {k: _bars_after(code, run_date)[1:] for k, code in BENCHES.items()}
        perf = compute_perf(ItemRef(it.code, it.stop_price, it.target1, it.code in st), _bars_after(it.code, run_date), bench_cache[run_date])
        values.append({
            "item_id": it.id, "run_id": it.run_id, "run_date": run_date, "code": it.code, "signal_type": it.signal_type,
            "scope": it.scope or "bi", "pool": it.pool or "main", "regime": regime, "score": it.score, **perf,
        })
        if ctx and i % 50 == 0:
            ctx.update(done=i + 1)
    if values:
        with session_scope() as db:
            for j in range(0, len(values), 500):
                stmt = insert(RecommendPerf).values(values[j : j + 500])
                db.execute(stmt.on_duplicate_key_update(**{k: stmt.inserted[k] for k in values[0] if k != "item_id"}))
    if ctx:
        ctx.update(done=len(todo), message=f"推荐跟踪已更新 {len(values)} 条", force=True)
    return {"updated": len(values)}


def _group(rows: list[RecommendPerf], key: str, h: int = 10) -> list[dict]:
    groups: dict[str, list[RecommendPerf]] = {}
    for r in rows:
        groups.setdefault(getattr(r, key) or "--", []).append(r)
    out = []
    for g, rs in groups.items():
        rets = [getattr(r, f"ret{h}") for r in rs if getattr(r, f"ret{h}") is not None]
        exs = [getattr(r, f"ex1000_{h}") for r in rs if getattr(r, f"ex1000_{h}") is not None]
        out.append({
            "group": g, "n": len(rs), "n_with_data": len(rets),
            "avg_ret": round(sum(rets) / len(rets), 2) if rets else None,
            "avg_excess": round(sum(exs) / len(exs), 2) if exs else None,
            "win_rate": round(sum(1 for x in rets if x > 0) / len(rets), 3) if rets else None,
        })
    return sorted(out, key=lambda x: x["group"])


def tracking_summary(days: int = 180) -> dict:
    since = date.today() - timedelta(days=days)
    with session_scope() as db:
        rows = db.execute(select(RecommendPerf).where(RecommendPerf.run_date >= since)).scalars().all()
    valid = [r for r in rows if not r.blocked]
    kpis = {}
    for h in HORIZONS:
        rets = [getattr(r, f"ret{h}") for r in valid if r.pool == "main" and getattr(r, f"ret{h}") is not None]
        ex3 = [getattr(r, f"ex300_{h}") for r in valid if r.pool == "main" and getattr(r, f"ex300_{h}") is not None]
        ex10 = [getattr(r, f"ex1000_{h}") for r in valid if r.pool == "main" and getattr(r, f"ex1000_{h}") is not None]
        kpis[str(h)] = {
            "n": len(rets),
            "avg_ret": round(sum(rets) / len(rets), 2) if rets else None,
            "win_rate": round(sum(1 for x in rets if x > 0) / len(rets), 3) if rets else None,
            "avg_ex300": round(sum(ex3) / len(ex3), 2) if ex3 else None,
            "avg_ex1000": round(sum(ex10) / len(ex10), 2) if ex10 else None,
            "beat_rate": round(sum(1 for x in ex10 if x > 0) / len(ex10), 3) if ex10 else None,
        }
    hits = [r.first_hit for r in valid if r.pool == "main" and r.days >= 5]
    runs: dict[int, list[RecommendPerf]] = {}
    for r in valid:
        runs.setdefault(r.run_id, []).append(r)
    run_rows = []
    curve_acc = 0.0
    curve = []
    for run_id in sorted(runs, key=lambda k: runs[k][0].run_date):
        rs = runs[run_id]
        main = [r for r in rs if r.pool == "main"]
        pairs10 = [(r.score, r.ret10) for r in main if r.ret10 is not None]
        pairs5 = [(r.score, r.ret5) for r in main if r.ret5 is not None]
        ic10 = spearman([a for a, _ in pairs10], [b for _, b in pairs10])
        ic5 = spearman([a for a, _ in pairs5], [b for _, b in pairs5])
        ex5 = [r.ex1000_5 for r in main if r.ex1000_5 is not None]
        avg_ex5 = sum(ex5) / len(ex5) if ex5 else None
        if avg_ex5 is not None:
            curve_acc += avg_ex5
            curve.append({"date": rs[0].run_date.isoformat(), "avg_ex5": round(avg_ex5, 2), "cum": round(curve_acc, 2)})

        def avg(field: str) -> float | None:
            xs = [getattr(r, field) for r in main if getattr(r, field) is not None]
            return round(sum(xs) / len(xs), 2) if xs else None

        run_rows.append({
            "run_id": run_id, "run_date": rs[0].run_date.isoformat(), "regime": rs[0].regime, "n_main": len(main),
            "n_watch": len(rs) - len(main), "days": max((r.days for r in rs), default=0),
            "avg_ret5": avg("ret5"), "avg_ret10": avg("ret10"), "avg_ret20": avg("ret20"),
            "avg_ex5": avg("ex1000_5"), "avg_ex10": avg("ex1000_10"), "avg_ex20": avg("ex1000_20"),
            "ic5": round(ic5, 3) if ic5 is not None else None, "ic10": round(ic10, 3) if ic10 is not None else None,
        })
    ics = [r["ic10"] for r in run_rows if r["ic10"] is not None] or [r["ic5"] for r in run_rows if r["ic5"] is not None]
    return {
        "kpis": kpis,
        "first_hit": {"stop": hits.count("stop"), "target": hits.count("target"), "none": hits.count(None)},
        "ic": {
            "mean": round(sum(ics) / len(ics), 3) if ics else None,
            "positive_ratio": round(sum(1 for x in ics if x > 0) / len(ics), 3) if ics else None,
            "n_runs": len(ics),
            "horizon": 10 if any(r["ic10"] is not None for r in run_rows) else 5,
        },
        "by_signal": _group([r for r in valid if r.pool == "main"], "signal_type"),
        "by_scope": _group([r for r in valid if r.pool == "main"], "scope"),
        "by_pool": _group(valid, "pool"),
        "by_regime": _group([r for r in valid if r.pool == "main"], "regime"),
        "curve": curve,
        "runs": sorted(run_rows, key=lambda x: x["run_date"], reverse=True),
        "total_items": len(rows),
        "blocked": sum(1 for r in rows if r.blocked),
    }


def run_perf(run_id: int) -> list[dict]:
    with session_scope() as db:
        rows = db.execute(
            select(RecommendPerf, RecommendItem.name, RecommendItem.rank)
            .join(RecommendItem, RecommendItem.id == RecommendPerf.item_id)
            .where(RecommendPerf.run_id == run_id).order_by(RecommendPerf.pool, RecommendItem.rank)
        ).all()
        return [
            {
                "item_id": p.item_id, "code": p.code, "name": name, "rank": rank, "pool": p.pool, "signal_type": p.signal_type,
                "scope": p.scope, "score": p.score, "entry_date": p.entry_date.isoformat() if p.entry_date else None,
                "entry_price": p.entry_price, "blocked": p.blocked, "days": p.days,
                **{f"ret{h}": getattr(p, f"ret{h}") for h in HORIZONS},
                **{f"ex1000_{h}": getattr(p, f"ex1000_{h}") for h in HORIZONS},
                "max_drawdown": p.max_drawdown, "first_hit": p.first_hit,
            }
            for p, name, rank in rows
        ]
