"""智能推荐：基本面与风险硬过滤 → 流动性过滤 → 全市场缠论 → 综合打分（缠论 + 基本面 + 板块）× 市场温度系数。

结果分两个池：
- 主推荐（main）：已确认的买点；
- 观察池（watch）：所在笔或线段尚未确认的买点，只观察，不写回 App。
"""

import asyncio
import logging
from datetime import date

from sqlalchemy import func, select

from app.analysis.fundamental import FundamentalView, load_views
from app.analysis.market_env import REGIME_NAMES, compute_market_env, latest_market_env, sector_strength_map
from app.chan import TYPE_NAMES, Signal
from app.db.models import KlineDaily, RecommendItem, RecommendRun, StockBasic
from app.db.session import session_scope
from app.providers import create_provider
from app.services.ashare_rules import volume_lot
from app.services.chan_service import StockAnalysis, analyze_stock, chan_config, save_signals, save_snapshot
from app.services.jobs import JobContext
from app.services.strategy_config import get_active_params
from app.services.sync import sync_finance_details

logger = logging.getLogger(__name__)

SCOPE_NAMES = {"bi": "笔级别", "seg": "线段级别"}


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def avg_amount_20(a: StockAnalysis) -> float | None:
    """近 20 日平均成交额（元）。公开接口的历史日线没有成交额，用成交量 × 收盘价估算。"""
    closes, vols = a.day.closes[-20:], a.day.volumes[-20:]
    if not closes or not any(vols):
        return None
    lot = volume_lot(a.code)
    return sum(c * v * lot for c, v in zip(closes, vols)) / len(closes)


def _pick_signal(a: StockAnalysis, params: dict, confirmed: bool | None) -> Signal | None:
    """在近期有效买点中选一个：优先最新，其次信号权重（线段级别加权）。"""
    weights = params["signal_weights"]
    price = a.day.last_close
    best, best_key = None, None
    for s in a.day.recent_signals(params["signal_recent_bars"], buy_only=True):
        if confirmed is not None and s.confirmed != confirmed:
            continue
        if s.stop_price is None or price is None or price <= s.stop_price:
            continue
        w = weights.get(s.type, 0.5) * (params["scope_weight"] if s.scope == "seg" else 1.0)
        key = (s.raw_idx, w)
        if best_key is None or key > best_key:
            best, best_key = s, key
    return best


def score_candidate(a: StockAnalysis, fund: FundamentalView, sector_strength: float, params: dict, regime: str,
                    signal: Signal | None = None) -> dict | None:
    best = signal
    pool = "main"
    if best is None:
        if params.get("recommend_confirmed_only", True):
            best = _pick_signal(a, params, confirmed=True)
            if best is None:
                best, pool = _pick_signal(a, params, confirmed=False), "watch"
        else:
            best = _pick_signal(a, params, confirmed=None)
    elif not best.confirmed and params.get("recommend_confirmed_only", True):
        pool = "watch"
    if best is None:
        return None
    price = a.day.last_close
    risk = price - best.stop_price
    rr = (best.target1 - price) / risk if best.target1 and risk > 0 else 0.0
    if rr <= 0:
        return None  # 价格已经越过目标一，这个买点的空间已经兑现
    div = best.extra.get("divergence") or {}
    strong = bool(div.get("strong"))
    type_w = params["signal_weights"].get(best.type, 0.5) * (params["scope_weight"] if best.scope == "seg" else 1.0)
    chan = 100 * _clamp(
        0.45 * _clamp(type_w)
        + 0.25 * best.strength
        + 0.20 * (a.view.resonance + 1) / 2
        + 0.10 * (1.0 if best.confirmed else 0.0)
    )
    reasons = [f"日线{SCOPE_NAMES.get(best.scope, '')}{TYPE_NAMES[best.type]}（{best.dt.isoformat()}）：{best.desc}"]
    if strong:
        reasons.append(f"强背驰：{div.get('agree', 0)} 项指标同时背驰")
    if not best.confirmed:
        reasons.append("信号所在的笔或线段尚未确认，放入观察池")
    reasons.extend(a.view.notes)
    if rr < params["min_reward_risk"]:
        chan *= 0.8
        reasons.append(f"按现价计算盈亏比 {rr:.1f}，低于 {params['min_reward_risk']}")
    else:
        reasons.append(f"按现价计算盈亏比 {rr:.1f}")
    reasons.extend(fund.highlights)
    risk_labels = fund.metrics.get("risk_labels") or []
    if risk_labels:
        reasons.append("风险提示（已扣分）：" + "、".join(risk_labels))
    amt = avg_amount_20(a)
    if amt:
        reasons.append(f"近 20 日日均成交额 {amt / 1e8:.1f} 亿元")
    w = params["weights"]
    total = (w["chan"] * chan + w["fund"] * fund.score + w["sector"] * sector_strength) * params["regime_multiplier"].get(regime, 1.0)
    return {
        "code": a.code,
        "score": round(total, 2),
        "chan_score": round(chan, 2),
        "fund_score": round(fund.score, 2),
        "sector_score": round(sector_strength, 2),
        "signal": best,
        "pool": pool,
        "strong_div": strong,
        "avg_amount": amt,
        "risk_labels": risk_labels,
        "price": price,
        "rr": round(rr, 2),
        "reasons": reasons,
    }


def _scan(codes: list[str], views: dict[str, FundamentalView], sectors: dict[str, float], industries: dict[str, str | None],
          params: dict, regime: str, calc_date: date, ctx: JobContext, stats: dict) -> list[tuple[dict, StockAnalysis]]:
    cfg = chan_config(params)
    results = []
    for i, code in enumerate(codes):
        try:
            a = analyze_stock(code, cfg=cfg)
        except Exception:
            logger.exception("分析 %s 失败", code)
            a = None
        if a is not None:
            n = len(a.day.dates)
            save_signals(code, [s for s in a.day.signals if n - 1 - s.raw_idx <= 30], calc_date)
            if a.week is not None:
                wn = len(a.week.dates)
                save_signals(code, [s for s in a.week.signals if wn - 1 - s.raw_idx <= 8], calc_date)
            amt = avg_amount_20(a)
            float_mv = views[code].metrics.get("float_mv")
            if amt is not None and amt < params["min_avg_amount"]:
                stats["illiquid"] += 1
            elif float_mv is not None and float_mv < params["min_float_mv"]:
                stats["small_cap"] += 1
            else:
                cand = score_candidate(a, views[code], sectors.get(industries.get(code) or "", 50.0), params, regime)
                if cand:
                    results.append((cand, a))
        ctx.update(done=i + 1, message=f"缠论扫描 {i + 1}/{len(codes)}，候选 {len(results)}")
    return results


async def run_recommendation(ctx: JobContext) -> dict:
    params = get_active_params()
    with session_scope() as db:
        latest_bar = db.scalar(select(func.max(KlineDaily.trade_date)))
        basics = {s.code: s for s in db.execute(select(StockBasic).where(StockBasic.is_index.is_(False), StockBasic.active.is_(True))).scalars()}
    if latest_bar is None:
        raise RuntimeError("还没有 K 线数据，请先完成数据初始化")
    env = latest_market_env()
    if env is None or env["trade_date"] != latest_bar.isoformat():
        async with create_provider() as provider:
            await compute_market_env(provider)
        env = latest_market_env()
    regime = env["regime"]

    with session_scope() as db:
        run = RecommendRun(run_date=latest_bar, status="running", market_regime=regime, position_cap=env["position_cap"],
                           config={"weights": params["weights"], "signal_weights": params["signal_weights"],
                                   "min_avg_amount": params["min_avg_amount"], "min_float_mv": params["min_float_mv"]})
        db.add(run)
        db.flush()
        run_id = run.id

    views = load_views()
    passed = [c for c, v in views.items() if v.passed and v.score >= params["min_fund_score"]]
    sectors = sector_strength_map()
    industries = {c: s.industry for c, s in basics.items()}
    stats = {"illiquid": 0, "small_cap": 0}
    ctx.update(done=0, total=len(passed), message=f"基本面与风险通过 {len(passed)}/{len(views)}，开始缠论扫描", force=True)
    results = await asyncio.to_thread(_scan, passed, views, sectors, industries, params, regime, latest_bar, ctx, stats)
    main = sorted([x for x in results if x[0]["pool"] == "main"], key=lambda x: x[0]["score"], reverse=True)
    watch = sorted([x for x in results if x[0]["pool"] == "watch"], key=lambda x: x[0]["score"], reverse=True)
    scan_count = len(results)

    # 主推荐候选补拉三大报表（有缓存），用精细基本面重新打分，并补做依赖财报的硬过滤
    shortlist = main[: params["recommend_top_n"] * 2]
    if shortlist:
        try:
            await sync_finance_details([c["code"] for c, _ in shortlist], ctx,
                                       time_budget=params.get("finance_time_budget", 480))
        except Exception as exc:
            logger.warning("候选股财报拉取失败，沿用评分近似: %s", exc)
        ctx.update(message="用精细基本面重新打分", force=True)
        fresh_views = load_views([c["code"] for c, _ in shortlist])
        rescored = []
        for cand, a in shortlist:
            fv = fresh_views.get(cand["code"])
            if fv is None or not fv.passed:
                continue
            new = score_candidate(a, fv, sectors.get(industries.get(cand["code"]) or "", 50.0), params, regime, cand["signal"])
            if new:
                rescored.append((new, a))
        main = sorted(rescored, key=lambda x: x[0]["score"], reverse=True)

    top_main = main[: params["recommend_top_n"]]
    top_watch = watch[: params.get("watch_top_n", 30)]
    with session_scope() as db:
        for items in (top_main, top_watch):
            for rank, (cand, a) in enumerate(items, start=1):
                s = cand["signal"]
                b = basics.get(cand["code"])
                db.add(RecommendItem(
                    run_id=run_id, rank=rank, code=cand["code"], name=b.name if b else None, industry=b.industry if b else None,
                    score=cand["score"], chan_score=cand["chan_score"], fund_score=cand["fund_score"],
                    sector_score=cand["sector_score"], signal_type=s.type, signal_date=s.dt, price=cand["price"],
                    stop_price=s.stop_price, target1=s.target1, target2=s.target2, reasons=cand["reasons"],
                    pool=cand["pool"], scope=s.scope, confirmed=s.confirmed, strong_div=cand["strong_div"],
                    avg_amount=cand["avg_amount"], risk_labels=cand["risk_labels"],
                ))
        run = db.get(RecommendRun, run_id)
        run.status = "success"
        run.total_scanned = len(passed)
        run.total_selected = len(top_main)
        run.message = (
            f"市场{REGIME_NAMES.get(regime, regime)}，基本面与风险通过 {len(passed)} 只，"
            f"流动性不足 {stats['illiquid']} 只、流通市值过小 {stats['small_cap']} 只，"
            f"缠论候选 {scan_count} 只；主推荐 {len(top_main)} 只，观察池 {len(top_watch)} 只"
        )
    for _, a in top_main:
        save_snapshot(a, latest_bar)
    ctx.update(message=f"推荐完成：主推荐 {len(top_main)} 只，观察池 {len(top_watch)} 只", force=True)
    return {"run_id": run_id, "selected": len(top_main), "watch": len(top_watch), "candidates": scan_count,
            "scanned": len(passed), **stats}


def _item_dict(it: RecommendItem) -> dict:
    return {
        "rank": it.rank, "code": it.code, "name": it.name, "industry": it.industry, "score": it.score,
        "chan_score": it.chan_score, "fund_score": it.fund_score, "sector_score": it.sector_score,
        "signal_type": it.signal_type, "signal_name": TYPE_NAMES.get(it.signal_type), "signal_date": it.signal_date.isoformat(),
        "price": it.price, "stop_price": it.stop_price, "target1": it.target1, "target2": it.target2, "reasons": it.reasons,
        "pool": it.pool, "scope": it.scope, "scope_name": SCOPE_NAMES.get(it.scope, it.scope), "confirmed": it.confirmed,
        "strong_div": it.strong_div, "avg_amount": it.avg_amount, "risk_labels": it.risk_labels or [],
    }


def latest_run(run_id: int | None = None) -> dict | None:
    with session_scope() as db:
        q = select(RecommendRun).where(RecommendRun.status == "success")
        if run_id:
            q = select(RecommendRun).where(RecommendRun.id == run_id)
        run = db.execute(q.order_by(RecommendRun.id.desc()).limit(1)).scalars().first()
        if run is None:
            return None
        items = db.execute(select(RecommendItem).where(RecommendItem.run_id == run.id).order_by(RecommendItem.pool, RecommendItem.rank)).scalars().all()
        all_items = [_item_dict(it) for it in items]
        return {
            "run": {
                "id": run.id,
                "run_date": run.run_date.isoformat(),
                "market_regime": run.market_regime,
                "position_cap": run.position_cap,
                "total_scanned": run.total_scanned,
                "total_selected": run.total_selected,
                "message": run.message,
                "created_at": run.created_at.isoformat(),
            },
            "items": [x for x in all_items if x["pool"] == "main"],
            "watch": [x for x in all_items if x["pool"] == "watch"],
        }


def list_runs(limit: int = 30) -> list[dict]:
    with session_scope() as db:
        rows = db.execute(select(RecommendRun).order_by(RecommendRun.id.desc()).limit(limit)).scalars().all()
        return [
            {"id": r.id, "run_date": r.run_date.isoformat(), "status": r.status, "market_regime": r.market_regime,
             "total_selected": r.total_selected, "message": r.message}
            for r in rows
        ]
