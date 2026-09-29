"""点金术持仓：按「最多 10 只、等权」开仓，不设止损，每天按「是否涨破卖出线」给建议。和回测的口径一致。"""

import logging
from datetime import date

from sqlalchemy import func, select

from app.db.models import PositionEvent, PositionPlan, StockBasic
from app.db.session import session_scope
from app.services.position_strategy import OPEN_STATUSES, Advice, persist_advice, round_lot
from app.services.strategy_config import get_active_config
from app.services.value_data import load_series
from app.strategies import strategy_name
from app.strategies.dianjin import VARIANTS, evaluate

logger = logging.getLogger(__name__)

MAX_POSITIONS = 10
SIGNAL = "DJ"


def _open_count(key: str, exclude_code: str | None = None) -> int:
    with session_scope() as db:
        q = select(func.count()).select_from(PositionPlan).where(PositionPlan.strategy == key, PositionPlan.status.in_(OPEN_STATUSES))
        if exclude_code:
            q = q.where(PositionPlan.code != exclude_code)
        return db.scalar(q) or 0


async def preview(key: str, code: str) -> dict:
    p = VARIANTS[key]
    s = load_series(code)
    if s is None:
        return {"allowed": False, "warnings": ["不在价值池：2014 年以来股息率从未达到 3%"], "quantity": 0}
    ev = evaluate(s, p)
    warnings = []
    if not ev.passed:
        warnings.append(ev.summary)
    elif ev.zone != "buy":
        warnings.append(f"现在处于{ev.zone_name}（现价为 MA120 的 {ev.metrics['ratio']:.0%}），规则要求低于 {p.buy_ratio:.0%} 才买入")
    held = _open_count(key, exclude_code=code)
    if held >= MAX_POSITIONS:
        warnings.append(f"{p.name}已持有 {held} 只，达到上限 {MAX_POSITIONS} 只")
    _, params = get_active_config()
    equity = params["default_equity"]
    try:
        from app.services.sim_trade import account_equity

        equity = await account_equity() or equity
    except Exception:  # noqa: BLE001
        logger.debug("获取模拟账户资产失败", exc_info=True)
    price = s.close[-1]
    qty = round_lot(equity / MAX_POSITIONS / price) if price else 0
    if qty == 0:
        warnings.append("按等权计算不足 100 股")
    return {
        "strategy": key, "strategy_name": p.name, "code": code, "price": round(price, 3), "quantity": qty,
        "amount": round(qty * price, 2), "equity": round(equity, 2), "held": held, "max_positions": MAX_POSITIONS,
        "zone": ev.zone, "zone_name": ev.zone_name, "summary": ev.summary, "metrics": ev.metrics,
        "allowed": not warnings, "warnings": warnings,
    }


def open_plan(key: str, code: str, quantity: int, price: float, linked_sim: bool) -> int:
    p = VARIANTS[key]
    s = load_series(code)
    sell_line = evaluate(s, p).metrics.get("sell_line") if s else None
    config_id, _ = get_active_config()
    with session_scope() as db:
        sb = db.get(StockBasic, code)
        plan = PositionPlan(
            code=code, name=sb.name if sb else None, status="Initial", strategy=key, entry_signal=SIGNAL,
            entry_signal_date=s.dates[-1] if s else date.today(), entry_date=date.today(), entry_price=price,
            quantity=quantity, remaining_qty=0 if linked_sim else quantity,
            # 点金术不设止损：止损记为 0，1R 记为买入价（R 倍数即收益率），卖出线记在目标一
            r_value=price, initial_stop=0.0, current_stop=0.0, stop_source="不设止损，涨破卖出线卖出",
            target1=sell_line, highest_price=price, config_id=config_id, linked_sim=linked_sim,
        )
        db.add(plan)
        db.flush()
        db.add(PositionEvent(plan_id=plan.id, event_type="open", level="execute", trade_date=plan.entry_date, price=price,
                             quantity=quantity, reason=f"按{p.name}开仓：低于 MA120 的 {p.buy_ratio:.0%}"))
        return plan.id


def evaluate_position(plan: PositionPlan) -> tuple[Advice, date, float] | None:
    p = VARIANTS.get(plan.strategy)
    s = load_series(plan.code)
    if p is None or s is None or not s.dates:
        return None
    ev = evaluate(s, p)
    m = ev.metrics
    adv = Advice()
    if m.get("ratio") is not None and m["ratio"] > p.sell_ratio:
        adv.escalate("close", plan.remaining_qty, f"收盘价为 MA120 的 {m['ratio']:.0%}，涨破卖出线 {m['sell_line']:.2f}")
    elif m.get("sell_line"):
        adv.reasons.append(f"距卖出线 {m['sell_line']:.2f} 还有 {m['sell_line'] / m['price'] - 1:+.1%}；点金术不设止损")
    if not ev.passed:
        adv.reasons.append(f"已不再满足{strategy_name(plan.strategy)}的筛选条件（{ev.summary}），按规则继续持有到卖出线")
    with session_scope() as db:
        row = db.get(PositionPlan, plan.id)
        if row is not None and m.get("sell_line"):
            row.target1 = m["sell_line"]
    return adv, s.dates[-1], s.close[-1]


def evaluate_and_persist(plan: PositionPlan) -> dict | None:
    out = evaluate_position(plan)
    if out is None:
        return None
    adv, today, close = out
    persist_advice(plan.id, adv, today, close)
    return {"plan_id": plan.id, "code": plan.code, **adv.to_dict()}
