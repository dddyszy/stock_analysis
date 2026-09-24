from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.analysis.market_env import latest_market_env
from app.db.models import PositionEvent, PositionPlan
from app.db.session import session_scope
from app.providers.parsing import normalize_code
from app.services import sim_trade
from app.services.chan_service import StockAnalysis, analyze_stock
from app.services.jobs import start_job
from app.services.position_strategy import (
    OPEN_STATUSES,
    build_entry_plan,
    evaluate_all,
    event_to_dict,
    open_plan,
    plan_to_dict,
)
from app.services.strategy_config import get_active_params

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class EntryRequest(BaseModel):
    code: str
    signal_type: str | None = None
    signal_date: date | None = None
    entry_price: float | None = None
    quantity: int | None = None
    place_order: bool = True
    linked_sim: bool = True


def _pick_signal(a: StockAnalysis, signal_type: str | None, signal_date: date | None, params: dict):
    buys = [s for s in a.day.signals if s.is_buy]
    if signal_type and signal_date:
        for s in buys:
            if s.type == signal_type and s.dt == signal_date:
                return s
    recent = a.day.recent_signals(params["signal_recent_bars"], buy_only=True)
    if signal_type:
        recent = [s for s in recent if s.type == signal_type] or recent
    return recent[-1] if recent else None


async def _entry(req: EntryRequest):
    code = normalize_code(req.code) or req.code
    a = analyze_stock(code)
    if a is None:
        raise HTTPException(404, "K 线数据不足")
    params = get_active_params()
    sig = _pick_signal(a, req.signal_type, req.signal_date, params)
    if sig is None:
        raise HTTPException(400, "近期没有有效的买点信号")
    env = latest_market_env()
    cap = env["position_cap"] if env else params["regime_caps"]["neutral"]
    equity = params["default_equity"]
    try:
        equity = await sim_trade.account_equity() or equity
    except Exception:
        pass
    return a, build_entry_plan(a, sig, equity, params, cap, req.entry_price)


@router.post("/entry-preview")
async def entry_preview(req: EntryRequest) -> dict:
    _, plan = await _entry(req)
    return plan.to_dict()


@router.post("/plans")
async def create_plan(req: EntryRequest) -> dict:
    _, entry = await _entry(req)
    qty = req.quantity or entry.quantity
    if qty <= 0 or qty % 100:
        raise HTTPException(400, "数量必须为 100 的整数倍且大于 0")
    if not entry.allowed and req.quantity is None:
        raise HTTPException(400, "；".join(entry.warnings) or "不满足开仓条件")
    plan_id = open_plan(entry, quantity=qty, entry_price=req.entry_price, linked_sim=req.linked_sim)
    order = None
    if req.linked_sim and req.place_order:
        try:
            order = await sim_trade.place_order(entry.code, "buy", qty, price=req.entry_price, plan_id=plan_id,
                                                signal_type=entry.signal_type, source="strategy")
        except Exception as exc:
            with session_scope() as db:
                plan = db.get(PositionPlan, plan_id)
                plan.status, plan.closed_at = "Closed", datetime.now()
                db.add(PositionEvent(plan_id=plan_id, event_type="cancel", level="info", trade_date=date.today(),
                                     reason=f"下单失败，计划作废：{str(exc)[:200]}"))
            raise HTTPException(400, f"模拟盘下单失败：{exc}")
    return {"plan_id": plan_id, "entry": entry.to_dict(), "order": order}


@router.get("/plans")
def plans(status: str = "open") -> list[dict]:
    with session_scope() as db:
        q = select(PositionPlan)
        if status == "open":
            q = q.where(PositionPlan.status.in_(OPEN_STATUSES))
        elif status == "closed":
            q = q.where(PositionPlan.status == "Closed")
        rows = db.execute(q.order_by(PositionPlan.id.desc())).scalars().all()
        return [plan_to_dict(p) for p in rows]


@router.get("/plans/{plan_id}")
def plan_detail(plan_id: int) -> dict:
    with session_scope() as db:
        p = db.get(PositionPlan, plan_id)
        if p is None:
            raise HTTPException(404, "持仓计划不存在")
        events = db.execute(select(PositionEvent).where(PositionEvent.plan_id == plan_id).order_by(PositionEvent.id.desc())).scalars().all()
        return {**plan_to_dict(p), "events": [event_to_dict(e) for e in events]}


@router.post("/plans/{plan_id}/execute")
async def execute_advice(plan_id: int) -> dict:
    """按最近一次建议下单（需要用户在前端确认后调用）。"""
    with session_scope() as db:
        p = db.get(PositionPlan, plan_id)
        if p is None:
            raise HTTPException(404, "持仓计划不存在")
        advice = p.last_advice or {}
        code, remaining, entry_signal = p.code, p.remaining_qty, p.entry_signal
    action = advice.get("action")
    if action in ("reduce", "close"):
        qty = remaining if action == "close" else min(remaining, int(advice.get("quantity") or 0))
        qty = qty // 100 * 100
        if qty <= 0:
            raise HTTPException(400, "没有可卖数量")
        return await sim_trade.place_order(code, "sell", qty, plan_id=plan_id, signal_type=entry_signal, source="strategy")
    if action == "add":
        add = advice.get("add_plan") or {}
        _, entry = await _entry(EntryRequest(code=code, signal_type=add.get("signal_type"),
                                             signal_date=date.fromisoformat(add["signal_date"]) if add.get("signal_date") else None))
        if entry.quantity <= 0:
            raise HTTPException(400, "；".join(entry.warnings) or "加仓数量为 0")
        with session_scope() as db:
            db.add(PositionEvent(plan_id=plan_id, event_type="add", level="execute", trade_date=date.today(), price=entry.entry_price,
                                 quantity=entry.quantity, reason=f"按{entry.signal_type}加仓", detail=entry.to_dict()))
        return await sim_trade.place_order(code, "buy", entry.quantity, plan_id=plan_id, signal_type=entry.signal_type, source="strategy")
    raise HTTPException(400, "当前建议为持有，无需下单")


class ManualClose(BaseModel):
    price: float | None = None
    reason: str | None = None


@router.post("/plans/{plan_id}/close-manual")
def close_manual(plan_id: int, body: ManualClose) -> dict:
    with session_scope() as db:
        p = db.get(PositionPlan, plan_id)
        if p is None:
            raise HTTPException(404, "持仓计划不存在")
        if body.price and p.remaining_qty:
            p.realized_pnl = (p.realized_pnl or 0) + (body.price - p.entry_price) * p.remaining_qty
        p.remaining_qty = 0
        p.status, p.closed_at = "Closed", datetime.now()
        db.add(PositionEvent(plan_id=plan_id, event_type="close", level="execute", trade_date=date.today(), price=body.price,
                             reason=body.reason or "手动关闭计划"))
    return {"ok": True}


@router.post("/evaluate")
async def evaluate() -> dict:
    start_job("evaluate_positions", evaluate_all)
    return {"started": True}


@router.get("/events")
def events(limit: int = 100, level: str | None = None) -> list[dict]:
    with session_scope() as db:
        q = select(PositionEvent, PositionPlan.code, PositionPlan.name).join(PositionPlan, PositionPlan.id == PositionEvent.plan_id)
        if level:
            q = q.where(PositionEvent.level == level)
        rows = db.execute(q.order_by(PositionEvent.id.desc()).limit(limit)).all()
        return [{**event_to_dict(e), "code": code, "name": name} for e, code, name in rows]
