from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.models import PositionEvent, PositionPlan
from app.db.session import session_scope
from app.services import dianjin_positions, sim_trade, strategy_scan
from app.strategies.dianjin import VARIANTS

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("")
def strategies() -> list[dict]:
    return strategy_scan.strategy_list()


@router.get("/backtest")
def backtest() -> dict | None:
    return strategy_scan.latest_backtest()


@router.get("/{key}/screen")
def screen(key: str, zone: str | None = None, passed_only: bool = True) -> dict:
    try:
        return strategy_scan.screen(key, zone, passed_only)
    except KeyError:
        raise HTTPException(404, f"没有这个策略：{key}")


@router.get("/stock/{code}")
def stock_views(code: str) -> list[dict]:
    return strategy_scan.stock_views(code)


class DianjinEntry(BaseModel):
    code: str
    quantity: int
    entry_price: float | None = None
    place_order: bool = True
    override: bool = False  # 不满足开仓条件时，用户已在前端看过警告并确认


@router.post("/{key}/entry-preview")
async def entry_preview(key: str, body: dict) -> dict:
    if key not in VARIANTS:
        raise HTTPException(404, f"没有这个策略：{key}")
    return await dianjin_positions.preview(key, body.get("code", ""))


@router.post("/{key}/positions")
async def open_position(key: str, body: DianjinEntry) -> dict:
    """开仓（需要用户在前端确认后调用）；place_order 时同时提交模拟盘买单。"""
    if key not in VARIANTS:
        raise HTTPException(404, f"没有这个策略：{key}")
    pv = await dianjin_positions.preview(key, body.code)
    if not pv["allowed"] and not body.override:
        raise HTTPException(400, "；".join(pv["warnings"]) or "不满足开仓条件")
    if body.quantity <= 0 or body.quantity % 100:
        raise HTTPException(400, "数量必须为 100 的整数倍且大于 0")
    linked = body.place_order and body.code.startswith(("sh", "sz"))
    price = body.entry_price or pv["price"]
    plan_id = dianjin_positions.open_plan(key, body.code, body.quantity, price, linked)
    order = None
    if linked:
        try:
            order = await sim_trade.place_order(body.code, "buy", body.quantity, price=body.entry_price, plan_id=plan_id,
                                                signal_type=dianjin_positions.SIGNAL, source="strategy", strategy=key)
        except Exception as exc:
            with session_scope() as db:
                plan = db.get(PositionPlan, plan_id)
                plan.status, plan.closed_at = "Closed", datetime.now()
                db.add(PositionEvent(plan_id=plan_id, event_type="cancel", level="info", trade_date=date.today(),
                                     reason=f"下单失败，计划作废：{str(exc)[:200]}"))
            raise HTTPException(400, f"模拟盘下单失败：{exc}")
    return {"plan_id": plan_id, "order": order}
