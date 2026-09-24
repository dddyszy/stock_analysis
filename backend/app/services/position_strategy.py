"""持仓策略：止损由结构决定，仓位由止损距离决定，止盈由卖点和跟踪止损决定。

每日评估顺序：硬止损 → 结构/跟踪止损 → 环境止损 → 结构卖点 → 跟踪止损上移 → 时间止损 → 加仓判断。
建议只是建议，真正的数量变化在成交后通过 apply_fill 落到持仓计划上。
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select

from app.analysis.market_env import latest_market_env
from app.chan import TYPE_NAMES, ChanResult, Signal
from app.chan.types import UP
from app.db.models import PositionEvent, PositionPlan, StockBasic
from app.db.session import session_scope
from app.services.ashare_rules import is_limit_down
from app.services.chan_service import StockAnalysis, analyze_stock
from app.services.jobs import JobContext
from app.services.strategy_config import get_active_config

logger = logging.getLogger(__name__)

LOT = 100
OPEN_STATUSES = ("Initial", "Breakeven", "Trailing", "Reduced")
STATUS_NAMES = {"Initial": "初始", "Breakeven": "保本", "Trailing": "跟踪", "Reduced": "已减仓", "Closed": "已清仓"}
ACTION_NAMES = {"hold": "持有", "add": "加仓", "reduce": "减仓", "close": "清仓"}


def round_lot(qty: float) -> int:
    return max(0, int(qty // LOT) * LOT)


def atr(result: ChanResult, period: int = 14) -> float | None:
    h, l, c = result.highs, result.lows, result.closes
    if len(c) <= period:
        return None
    trs = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(len(c) - period, len(c))]
    return sum(trs) / period


def stop_buffer(result: ChanResult, price: float, params: dict) -> float:
    a = atr(result)
    if a is None:
        return price * params["stop_buffer_pct"]
    return min(max(a * params["stop_buffer_atr"], price * 0.005), price * 0.03)


# ---------- 开仓计划 ----------


@dataclass
class EntryPlan:
    code: str
    signal_type: str
    signal_date: date
    entry_price: float
    structural_stop: float
    stop: float
    r_value: float
    target1: float | None
    target2: float | None
    reward_risk: float
    quantity: int
    position_value: float
    allowed: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["signal_date"] = self.signal_date.isoformat()
        d["signal_name"] = TYPE_NAMES.get(self.signal_type, self.signal_type)
        return d


def current_exposure(exclude_code: str | None = None) -> float:
    with session_scope() as db:
        plans = db.execute(select(PositionPlan).where(PositionPlan.status.in_(OPEN_STATUSES))).scalars().all()
        return sum(p.remaining_qty * p.entry_price for p in plans if p.code != exclude_code)


def build_entry_plan(a: StockAnalysis, signal: Signal, equity: float, params: dict, position_cap: float,
                     entry_price: float | None = None) -> EntryPlan:
    price = entry_price or a.day.last_close
    structural = signal.stop_price if signal.stop_price is not None else signal.price
    stop = structural - stop_buffer(a.day, price, params)
    warnings: list[str] = []
    allowed = True
    r = price - stop
    if r <= 0:
        return EntryPlan(a.code, signal.type, signal.dt, price, structural, stop, 0, signal.target1, signal.target2, 0, 0, 0,
                         False, ["现价已低于止损位，信号失效"])
    rr = (signal.target1 - price) / r if signal.target1 else 0.0
    if rr < params["min_reward_risk"]:
        allowed = False
        warnings.append(f"盈亏比 {rr:.2f} 低于 {params['min_reward_risk']}，不开仓")
    qty = equity * params["risk_per_trade"] / r
    stop_dist = r / price
    if stop_dist > params["max_stop_distance"]:
        if params["wide_stop_action"] == "skip":
            allowed = False
            warnings.append(f"止损距离 {stop_dist:.1%} 超过 {params['max_stop_distance']:.0%}，不开仓")
        else:
            qty /= 2
            warnings.append(f"止损距离 {stop_dist:.1%} 超过 {params['max_stop_distance']:.0%}，仓位减半")
    qty = min(qty, equity * params["max_single_position"] / price)
    room = equity * position_cap - current_exposure(exclude_code=a.code)
    if room <= 0:
        allowed = False
        warnings.append(f"已达市场温度对应的总仓位上限 {position_cap:.0%}")
        qty = 0
    else:
        qty = min(qty, room / price)
    qty = round_lot(qty)
    if qty == 0 and allowed:
        allowed = False
        warnings.append("按风险计算不足 100 股")
    if not signal.confirmed:
        warnings.append("信号所在笔尚未确认，建议等待确认后再执行")
    return EntryPlan(a.code, signal.type, signal.dt, round(price, 3), round(structural, 3), round(stop, 3), round(r, 3),
                     signal.target1, signal.target2, round(rr, 2), qty, round(qty * price, 2), allowed, warnings)


def open_plan(entry: EntryPlan, quantity: int | None = None, entry_price: float | None = None, entry_date: date | None = None,
              linked_sim: bool = True) -> int:
    config_id, _ = get_active_config()
    qty = quantity if quantity is not None else entry.quantity
    price = entry_price or entry.entry_price
    with session_scope() as db:
        sb = db.get(StockBasic, entry.code)
        plan = PositionPlan(
            code=entry.code, name=sb.name if sb else None, status="Initial", entry_signal=entry.signal_type,
            entry_signal_date=entry.signal_date, entry_date=entry_date or date.today(), entry_price=price, quantity=qty,
            remaining_qty=qty if not linked_sim else 0, r_value=round(price - entry.stop, 3), initial_stop=entry.stop,
            current_stop=entry.stop, stop_source=f"{TYPE_NAMES.get(entry.signal_type)}结构止损", target1=entry.target1,
            target2=entry.target2, highest_price=price, config_id=config_id, linked_sim=linked_sim,
        )
        db.add(plan)
        db.flush()
        db.add(PositionEvent(plan_id=plan.id, event_type="open", level="execute", trade_date=plan.entry_date, price=price,
                             quantity=qty, reason=f"按{TYPE_NAMES.get(entry.signal_type)}开仓计划",
                             detail=entry.to_dict()))
        return plan.id


def apply_fill(plan_id: int, direction: str, qty: int, price: float, trade_date: date | None = None) -> None:
    """成交回写：买入增加剩余数量，卖出减少，并记录已实现盈亏。"""
    trade_date = trade_date or date.today()
    with session_scope() as db:
        plan = db.get(PositionPlan, plan_id)
        if plan is None:
            return
        if direction == "buy":
            old_value = plan.remaining_qty * plan.entry_price
            plan.remaining_qty += qty
            if plan.remaining_qty > 0 and old_value > 0:
                plan.entry_price = round((old_value + qty * price) / plan.remaining_qty, 3)
            elif plan.remaining_qty > 0:
                plan.entry_price = price
            plan.quantity = max(plan.quantity, plan.remaining_qty)
        else:
            sell = min(qty, plan.remaining_qty)
            plan.remaining_qty -= sell
            plan.realized_pnl = (plan.realized_pnl or 0) + (price - plan.entry_price) * sell
            if plan.remaining_qty == 0:
                plan.status = "Closed"
                plan.closed_at = datetime.now()
            elif plan.status in ("Initial", "Breakeven", "Trailing"):
                plan.status = "Reduced"
            plan.reduce_steps = (plan.reduce_steps or 0) + 1
        db.add(PositionEvent(plan_id=plan_id, event_type="fill", level="execute", trade_date=trade_date, price=price,
                             quantity=qty, reason=f"{'买入' if direction == 'buy' else '卖出'}成交 {qty} 股 @ {price}"))


# ---------- 每日评估 ----------


@dataclass
class Advice:
    action: str = "hold"
    quantity: int = 0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    new_stop: float | None = None
    stop_source: str | None = None
    new_status: str | None = None
    add_plan: dict | None = None

    def escalate(self, action: str, qty: int, reason: str) -> None:
        order = ["hold", "add", "reduce", "close"]
        if order.index(action) > order.index(self.action):
            self.action, self.quantity = action, qty
        elif action == self.action and action == "reduce":
            self.quantity = max(self.quantity, qty)
        self.reasons.append(reason)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "action_name": ACTION_NAMES[self.action],
            "quantity": self.quantity,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "new_stop": self.new_stop,
            "stop_source": self.stop_source,
            "new_status": self.new_status,
            "add_plan": self.add_plan,
        }


def _bar_index_on_or_after(result: ChanResult, d: date) -> int:
    for i, x in enumerate(result.dates):
        if x >= d:
            return i
    return len(result.dates)


def _events_reasons(plan_id: int) -> set[str]:
    with session_scope() as db:
        rows = db.execute(select(PositionEvent.detail).where(PositionEvent.plan_id == plan_id)).scalars().all()
    keys = set()
    for d in rows:
        if isinstance(d, dict):
            if d.get("key"):
                keys.add(d["key"])
            keys.update(d.get("keys") or [])
    return keys


def evaluate_plan(plan: PositionPlan, a: StockAnalysis, env: dict | None, params: dict, today: date | None = None,
                  exposure_ratio: float | None = None, is_st: bool = False) -> Advice:
    day = a.day
    today = today or day.dates[-1]
    close = day.closes[-1]
    high_today = day.highs[-1]
    adv = Advice()
    remaining = plan.remaining_qty
    original = max(plan.quantity, remaining)
    t1_locked = plan.entry_date >= today  # T+1：买入当天不能卖
    done_keys = _events_reasons(plan.id)
    entry_idx = _bar_index_on_or_after(day, plan.entry_date)
    buffer = stop_buffer(day, close, params)
    stop = plan.current_stop
    status = plan.status

    def raise_stop(new: float, source: str) -> None:
        nonlocal stop
        if new > stop + 1e-6:
            stop = round(new, 3)
            adv.new_stop, adv.stop_source = stop, source

    def batch_qty(step: int) -> int:
        ratios = params["batch_ratios"]
        if step < len(ratios):
            return min(remaining, max(LOT, round_lot(original * ratios[step])))
        return remaining

    if remaining <= 0:
        adv.reasons.append("尚无持仓数量（等待成交）")
        return adv

    # 1. 硬止损
    if close <= plan.entry_price * (1 - params["hard_stop_pct"]):
        adv.escalate("close", remaining, f"浮亏超过 {params['hard_stop_pct']:.0%}，触发硬止损")
    # 2. 结构 / 跟踪止损（按收盘价确认）
    elif close < stop:
        if status in ("Trailing",):
            adv.escalate("reduce", batch_qty(1), f"收盘 {close:.2f} 跌破跟踪止损 {stop:.2f}，按分批方案减仓")
            adv.new_status = "Reduced"
        else:
            name = {"Initial": "结构止损", "Breakeven": "保本止损", "Reduced": "跟踪止损（再次触发）"}.get(status, "止损")
            adv.escalate("close", remaining, f"收盘 {close:.2f} 跌破{name} {stop:.2f}")

    # 3. 环境止损
    regime = (env or {}).get("regime")
    if regime == "weak":
        confirmed_bottoms = [b.start.price for b in day.bis if b.direction == UP and b.confirmed]
        if confirmed_bottoms:
            raise_stop(confirmed_bottoms[-1] - buffer, "市场转弱，止损收紧到最近确认底分型")
        if exposure_ratio and env and exposure_ratio > env["position_cap"]:
            cut = 1 - env["position_cap"] / exposure_ratio
            qty = min(remaining, max(LOT, round_lot(remaining * cut)))
            adv.escalate("reduce", qty, f"市场弱势，总仓位 {exposure_ratio:.0%} 超过上限 {env['position_cap']:.0%}")

    # 4. 结构卖点（只看开仓之后出现的）
    week_up = a.view.week_uptrend
    for s in day.signals:
        if s.is_buy or s.raw_idx < entry_idx:
            continue
        key = f"sell:{s.type}:{s.dt.isoformat()}"
        if not s.confirmed:
            adv.warnings.append(f"日线{TYPE_NAMES[s.type]}预警（{s.dt}），等待确认")
            continue
        if key in done_keys:
            continue
        if s.type == "S1":
            qty = batch_qty(0)
            if week_up:
                base = round_lot(original * params["keep_base_ratio_week_uptrend"])
                qty = max(0, min(qty, remaining - base))
                if qty == 0:
                    adv.warnings.append("日线一卖，但周线仍在上涨结构，保留底仓")
                    continue
            adv.escalate("reduce", qty, f"日线一卖（{s.dt}）：{s.desc}")
        elif s.type == "S2":
            adv.escalate("reduce", batch_qty(1), f"日线二卖（{s.dt}）：{s.desc}")
        elif s.type == "S3":
            adv.escalate("close", remaining, f"日线三卖（{s.dt}）：{s.desc}")
        adv.new_status = adv.new_status or ("Closed" if adv.action == "close" else "Reduced")
    ws = a.view.week_sell
    if ws is not None and a.week is not None and ws.dt >= plan.entry_date:
        adv.escalate("close", remaining, f"周线{TYPE_NAMES[ws.type]}（{ws.dt}）")
    if plan.target1 and high_today >= plan.target1 and (plan.reduce_steps or 0) == 0 and "target1" not in done_keys:
        adv.escalate("reduce", batch_qty(0), f"到达目标一 {plan.target1:.2f}")

    # 5. 跟踪止损上移（只上移不下移）
    if status == "Initial" and close >= plan.entry_price + plan.r_value:
        raise_stop(plan.entry_price, "浮盈达到 1R，止损上移到成本价")
        adv.new_status = adv.new_status or "Breakeven"
    new_ups = [b for b in day.bis if b.direction == UP and b.confirmed and b.start_raw >= entry_idx]
    if new_ups:
        raise_stop(new_ups[-1].start.price - buffer, f"新的确认向上笔（起点 {new_ups[-1].start.dt}），止损上移到笔起点")
        if status in ("Breakeven",) or adv.new_status == "Breakeven":
            adv.new_status = "Trailing" if adv.action == "hold" else adv.new_status
    zs_above = [z for z in day.bi_zhongshus if z.start_raw >= entry_idx and z.zd > plan.entry_price]
    if zs_above:
        raise_stop(zs_above[-1].zd - buffer, "上方形成新中枢，止损上移到中枢下沿 ZD")

    # 6. 时间止损
    bars_held = len(day.dates) - 1 - entry_idx
    if status == "Initial" and bars_held >= params["time_stop_bars"] and not new_ups and "time_stop" not in done_keys:
        adv.escalate("reduce", min(remaining, max(LOT, round_lot(remaining * params["time_stop_reduce"]))),
                     f"持有 {bars_held} 根日线仍未走出确认的向上笔，触发时间止损")

    # 7. 加仓判断
    if adv.action == "hold" and plan.entry_signal == "B1" and stop >= plan.entry_price:
        adds = [s for s in day.recent_signals(3, buy_only=True) if s.type in ("B2", "B3") and s.confirmed and s.raw_idx > entry_idx]
        if adds:
            s = adds[-1]
            adv.escalate("add", 0, f"一买之后出现{TYPE_NAMES[s.type]}（{s.dt}），且止损已在成本价之上")
            adv.add_plan = {"signal_type": s.type, "signal_date": s.dt.isoformat(), "stop_price": s.stop_price}

    # A 股执行约束
    if t1_locked and adv.action in ("reduce", "close"):
        adv.warnings.append("T+1：今日买入的仓位不能卖出，建议次日执行")
        adv.action, adv.quantity = "hold", 0
    if adv.action in ("reduce", "close") and len(day.closes) > 1 and is_limit_down(day.closes[-2], close, plan.code, is_st):
        adv.warnings.append("收盘跌停，可能无法卖出，建议次日开盘执行")
    if not adv.reasons:
        adv.reasons.append("结构未破坏，继续持有")
    return adv


def persist_advice(plan_id: int, adv: Advice, today: date, close: float) -> None:
    with session_scope() as db:
        plan = db.get(PositionPlan, plan_id)
        if plan is None:
            return
        if adv.new_stop is not None and adv.new_stop > plan.current_stop:
            db.add(PositionEvent(plan_id=plan_id, event_type="stop_move", level="info", trade_date=today, price=adv.new_stop,
                                 reason=f"止损 {plan.current_stop:.2f} → {adv.new_stop:.2f}：{adv.stop_source}"))
            plan.current_stop = adv.new_stop
            plan.stop_source = adv.stop_source
        if adv.new_status in ("Breakeven", "Trailing") and plan.status in ("Initial", "Breakeven"):
            plan.status = adv.new_status
        plan.highest_price = max(plan.highest_price or close, close)
        plan.last_eval_date = today
        plan.last_advice = {**adv.to_dict(), "date": today.isoformat(), "close": close}
        for w in adv.warnings:
            db.add(PositionEvent(plan_id=plan_id, event_type="warning", level="warning", trade_date=today, price=close, reason=w[:255]))
        if adv.action != "hold":
            once_keys = []
            for r in adv.reasons:
                if r.startswith("到达目标一"):
                    once_keys.append("target1")
                elif "时间止损" in r:
                    once_keys.append("time_stop")
            detail = {"advice": adv.to_dict(), "keys": once_keys}
            db.add(PositionEvent(plan_id=plan_id, event_type="advice", level="execute", trade_date=today, price=close,
                                 quantity=adv.quantity, reason=f"{ACTION_NAMES[adv.action]}：{adv.reasons[0]}"[:255], detail=detail))
            sell_keys = []
            for r in adv.reasons:
                for t in ("一卖", "二卖", "三卖"):
                    if r.startswith(f"日线{t}"):
                        dt = r[r.find("（") + 1 : r.find("）")]
                        code = {"一卖": "S1", "二卖": "S2", "三卖": "S3"}[t]
                        sell_keys.append(f"sell:{code}:{dt}")
            for k in sell_keys:
                db.add(PositionEvent(plan_id=plan_id, event_type="signal", level="info", trade_date=today, price=close,
                                     reason=f"已处理卖点 {k}", detail={"key": k}))


async def evaluate_all(ctx: JobContext | None = None) -> dict:
    _, params = get_active_config()
    env = latest_market_env()
    with session_scope() as db:
        plans = db.execute(select(PositionPlan).where(PositionPlan.status.in_(OPEN_STATUSES))).scalars().all()
        st_codes = set(db.execute(select(StockBasic.code).where(StockBasic.is_st.is_(True))).scalars())
    equity = params["default_equity"]
    try:
        from app.services.sim_trade import account_equity

        equity = await account_equity() or equity
    except Exception:
        logger.debug("获取模拟账户资产失败", exc_info=True)
    exposure = sum(p.remaining_qty * p.entry_price for p in plans)
    exposure_ratio = exposure / equity if equity else None
    out = []
    if ctx:
        ctx.update(done=0, total=len(plans), message=f"评估 {len(plans)} 个持仓", force=True)
    for p in plans:
        a = analyze_stock(p.code)
        if a is None:
            continue
        today = a.day.dates[-1]
        adv = evaluate_plan(p, a, env, params, today, exposure_ratio, is_st=p.code in st_codes)
        persist_advice(p.id, adv, today, a.day.closes[-1])
        out.append({"plan_id": p.id, "code": p.code, **adv.to_dict()})
        if ctx:
            ctx.step(f"{p.code}: {ACTION_NAMES[adv.action]}")
    try:
        from app.services.app_sync import sync_price_alerts

        await sync_price_alerts()
    except Exception as exc:
        logger.warning("同步股价提醒失败: %s", exc)
    return {"evaluated": len(out), "actions": {k: sum(1 for x in out if x["action"] == k) for k in ACTION_NAMES}}


async def check_intraday_stops() -> list[dict]:
    """盘中轮询：用实时价检查硬止损和止损位，只记录预警，由用户确认后下单。"""
    from app.providers import create_provider

    _, params = get_active_config()
    with session_scope() as db:
        plans = db.execute(select(PositionPlan).where(PositionPlan.status.in_(OPEN_STATUSES), PositionPlan.remaining_qty > 0)).scalars().all()
    if not plans:
        return []
    async with create_provider() as provider:
        quotes = await provider.quotes([p.code for p in plans])
    alerts = []
    today = date.today()
    with session_scope() as db:
        for p in plans:
            q = quotes.get(p.code)
            if not q or not q.price:
                continue
            reason = None
            if q.price <= p.entry_price * (1 - params["hard_stop_pct"]):
                reason = f"盘中价 {q.price:.2f} 触发硬止损（-{params['hard_stop_pct']:.0%}）"
            elif q.price < p.current_stop:
                reason = f"盘中价 {q.price:.2f} 低于止损 {p.current_stop:.2f}，收盘确认后执行"
            if reason:
                exists = db.execute(select(PositionEvent).where(
                    PositionEvent.plan_id == p.id, PositionEvent.trade_date == today, PositionEvent.reason == reason[:255])).first()
                if not exists:
                    db.add(PositionEvent(plan_id=p.id, event_type="warning", level="warning", trade_date=today, price=q.price, reason=reason[:255]))
                alerts.append({"plan_id": p.id, "code": p.code, "price": q.price, "reason": reason})
    return alerts


def plan_to_dict(p: PositionPlan) -> dict:
    return {
        "id": p.id, "code": p.code, "name": p.name, "status": p.status, "status_name": STATUS_NAMES.get(p.status, p.status),
        "entry_signal": p.entry_signal, "entry_signal_name": TYPE_NAMES.get(p.entry_signal or "", p.entry_signal),
        "entry_signal_date": p.entry_signal_date.isoformat() if p.entry_signal_date else None,
        "entry_date": p.entry_date.isoformat(), "entry_price": p.entry_price, "quantity": p.quantity,
        "remaining_qty": p.remaining_qty, "r_value": p.r_value, "initial_stop": p.initial_stop, "current_stop": p.current_stop,
        "stop_source": p.stop_source, "target1": p.target1, "target2": p.target2, "highest_price": p.highest_price,
        "reduce_steps": p.reduce_steps, "linked_sim": p.linked_sim, "last_eval_date": p.last_eval_date.isoformat() if p.last_eval_date else None,
        "last_advice": p.last_advice, "realized_pnl": p.realized_pnl,
        "closed_at": p.closed_at.isoformat() if p.closed_at else None, "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def event_to_dict(e: PositionEvent) -> dict:
    return {
        "id": e.id, "plan_id": e.plan_id, "event_type": e.event_type, "level": e.level, "trade_date": e.trade_date.isoformat(),
        "price": e.price, "quantity": e.quantity, "reason": e.reason, "detail": e.detail,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }
