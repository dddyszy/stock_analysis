"""把结构候选的筛选规则逐层套到回测交易上，看每一层之后相对同条件随机入场的表现。

能按历史复现的：流动性（入场时的 20 日均成交额）、降级组合（regime_block）；
只能用当前快照的：基本面与风险过滤、基本面分（有未来函数，结果偏乐观，仅供参考）。
"""

from sqlalchemy import select

from app.analysis.fundamental import load_views
from app.db.models import BacktestTrade
from app.db.session import session_scope
from app.services.backtest import _stat, _year_regime, matched_edge
from app.services.strategy_config import get_active_params


def _load(run_id: int) -> tuple[list[dict], list[dict]]:
    with session_scope() as db:
        rows = db.execute(select(BacktestTrade).where(BacktestTrade.run_id == run_id)).scalars().all()
        trades = [{"code": t.code, "signal_type": t.signal_type, "entry_date": t.entry_date, "r_multiple": t.r_multiple,
                   "pnl_pct": t.pnl_pct, "excess": t.excess, "holding_days": t.holding_days, "tags": t.tags or {},
                   "is_control": t.is_control, "segment": t.segment} for t in rows]
    return [t for t in trades if not t["is_control"]], [t for t in trades if t["is_control"]]


def _amount(t: dict) -> float | None:
    return ((t["tags"].get("f") or {}).get("amount20"))


def candidate_rule_stages(run_id: int) -> list[dict]:
    params = get_active_params()
    sig, ctrl = _load(run_id)
    min_amount = params["min_avg_amount"] / 1e8
    blocked = set(params.get("regime_block") or [])
    views = load_views()
    fund_ok = {c for c, v in views.items() if v.passed and v.score >= params["min_fund_score"]}
    fund_score = {c: v.score for c, v in views.items()}

    liquid = lambda t: (a := _amount(t)) is not None and a >= min_amount  # noqa: E731
    not_blocked = lambda t: f"{t['signal_type']}|{t['tags'].get('regime', 'unknown')}" not in blocked  # noqa: E731
    fund = lambda t: t["code"] in fund_ok  # noqa: E731

    stages = [
        ("全部缠论买点", lambda t: True, lambda t: True),
        ("+ 流动性过滤", liquid, liquid),
        ("+ 排除降级组合（= 可复现的候选规则）", lambda t: liquid(t) and not_blocked(t), liquid),
        ("+ 基本面与风险过滤（当前快照，有未来函数）", lambda t: liquid(t) and not_blocked(t) and fund(t), lambda t: liquid(t) and fund(t)),
    ]
    out = []
    for name, keep_sig, keep_ctrl in stages:
        s = [t for t in sig if keep_sig(t)]
        c = [t for t in ctrl if keep_ctrl(t)]
        st = _stat(s)
        out.append({"stage": name, "trades": st.get("trades", 0), "win_rate": st.get("win_rate"), "avg_r": st.get("avg_r"),
                    "avg_excess": st.get("avg_excess"), "ctrl_trades": len(c), "ctrl_avg_r": _stat(c).get("avg_r"),
                    "edge": matched_edge(s, c, _year_regime),
                    "out_sample_r": _stat([t for t in s if t["segment"] == "out"]).get("avg_r")})

    # 综合分近似：在最后一层里按当前基本面分分三档，看分数高的是否更好
    last = [t for t in sig if liquid(t) and not_blocked(t) and fund(t)]
    ranked = sorted(last, key=lambda t: fund_score.get(t["code"], 0))
    n = len(ranked)
    ctrl_last = [t for t in ctrl if liquid(t) and fund(t)]
    for label, part in (("基本面分低档", ranked[: n // 3]), ("基本面分中档", ranked[n // 3 : 2 * n // 3]), ("基本面分高档", ranked[2 * n // 3 :])):
        st = _stat(part)
        out.append({"stage": f"最后一层 · {label}", "trades": st.get("trades", 0), "win_rate": st.get("win_rate"),
                    "avg_r": st.get("avg_r"), "avg_excess": st.get("avg_excess"), "ctrl_trades": len(ctrl_last),
                    "ctrl_avg_r": _stat(ctrl_last).get("avg_r"), "edge": matched_edge(part, ctrl_last, _year_regime),
                    "out_sample_r": _stat([t for t in part if t["segment"] == "out"]).get("avg_r")})
    return out
