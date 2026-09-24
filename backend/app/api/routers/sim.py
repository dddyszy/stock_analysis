from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models import SimOrder
from app.db.session import session_scope
from app.services import sim_trade

router = APIRouter(prefix="/api/sim", tags=["sim"])


async def _with_gateway(fn):
    gw = sim_trade.create_gateway()
    try:
        return await fn(gw)
    finally:
        await gw.close()


@router.get("/account")
async def account() -> dict:
    return await _with_gateway(lambda gw: gw.account())


@router.get("/positions")
async def positions() -> list[dict]:
    rows = await _with_gateway(lambda gw: gw.positions())
    return [{k: v for k, v in r.items() if k != "raw"} for r in rows]


@router.get("/profit")
async def profit() -> dict:
    return await _with_gateway(lambda gw: gw.profit())


@router.get("/orders")
def orders(limit: int = 200) -> list[dict]:
    with session_scope() as db:
        rows = db.execute(select(SimOrder).order_by(SimOrder.id.desc()).limit(limit)).scalars().all()
        return [sim_trade.order_to_dict(o) for o in rows]


class OrderRequest(BaseModel):
    code: str
    direction: str
    quantity: int
    price: float | None = None
    plan_id: int | None = None


@router.post("/orders")
async def place(req: OrderRequest) -> dict:
    return await sim_trade.place_order(req.code, req.direction, req.quantity, req.price, plan_id=req.plan_id)


@router.get("/limit-price")
async def limit_price(code: str, direction: str) -> dict:
    return {"price": await sim_trade.default_limit_price(code, direction)}


@router.post("/orders/{order_id}/cancel")
async def cancel(order_id: str) -> dict:
    return await sim_trade.cancel_order(order_id)


@router.post("/sync")
async def sync(range: str = "today") -> dict:
    return await sim_trade.sync_orders(range)


@router.post("/snapshot")
async def snapshot() -> dict:
    res = await sim_trade.snapshot()
    return {"positions": res["positions"], "account": {k: v for k, v in res["account"].items() if k != "raw"}}


@router.get("/stats")
def stats() -> dict:
    return sim_trade.stats()
