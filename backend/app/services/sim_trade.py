"""模拟盘：腾讯自选股 portfolio_paper_* 工具的网关 + 本地订单镜像 + 复盘统计。

MCP 约束：仅沪深 A 股、限价单、数量为 100 的整数倍。
mock 模式下使用 LocalSimGateway（仅用于开发联调，不代表真实撮合）。
"""

import logging
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert

from app.chan import TYPE_NAMES
from app.core.config import get_settings
from app.db.models import AppSyncState, PositionPlan, SimAccountDaily, SimOrder, SimPositionSnapshot, StockBasic
from app.db.session import session_scope
from app.mcp.client import McpSession
from app.providers import create_provider
from app.providers.parsing import find_records, normalize_code, pick, to_float, to_int
from app.services.ashare_rules import clamp_to_limits
from app.services.strategy_config import get_active_params

logger = logging.getLogger(__name__)

LOT = 100


class SimTradeError(Exception):
    pass


def _norm_direction(v: Any) -> str | None:
    s = str(v or "").strip().lower()
    if s in ("buy", "b", "1", "买", "买入", "证券买入"):
        return "buy"
    if s in ("sell", "s", "2", "卖", "卖出", "证券卖出"):
        return "sell"
    return None


def _norm_status(v: Any, filled: int | None = None, qty: int | None = None) -> str:
    s = str(v or "").strip().lower()
    if any(x in s for x in ("撤", "cancel", "withdraw")):
        return "cancelled"
    if any(x in s for x in ("废", "拒", "reject", "fail", "invalid")):
        return "rejected"
    if any(x in s for x in ("部成", "partial")):
        return "partial"
    if any(x in s for x in ("已成", "全部成交", "filled", "done", "deal", "success", "成交")):
        return "filled"
    if filled and qty and filled >= qty:
        return "filled"
    if filled:
        return "partial"
    return "pending"


class SimTradeGateway(ABC):
    @abstractmethod
    async def account(self) -> dict: ...

    @abstractmethod
    async def positions(self) -> list[dict]: ...

    @abstractmethod
    async def place(self, code: str, direction: str, price: float, quantity: int) -> dict: ...

    @abstractmethod
    async def cancel(self, order_id: str) -> dict: ...

    @abstractmethod
    async def history(self, range_: str = "today", month: str | None = None) -> list[dict]: ...

    @abstractmethod
    async def profit(self) -> dict: ...

    async def close(self) -> None:
        return None


class TencentMcpSimGateway(SimTradeGateway):
    def __init__(self) -> None:
        self.session = McpSession()

    async def close(self) -> None:
        await self.session.close()

    async def account(self) -> dict:
        data = await self.session.call("portfolio_paper_portfolio", {})
        src = data if isinstance(data, dict) else {}
        flat = src.get("portfolio") if isinstance(src.get("portfolio"), dict) else src
        return {
            "total_asset": to_float(pick(flat, "total_asset", "totalAsset", "总资产", "asset", "total_assets")),
            "cash": to_float(pick(flat, "available", "cash", "available_cash", "可用资金", "enable_balance", "availableFund")),
            "market_value": to_float(pick(flat, "market_value", "marketValue", "持仓市值", "stock_value", "position_value")),
            "total_pnl": to_float(pick(flat, "total_profit", "totalProfit", "总盈亏", "profit", "total_pnl")),
            "raw": src,
        }

    async def positions(self) -> list[dict]:
        data = await self.session.call("portfolio_paper_positions", {})
        out = []
        for r in find_records(data, prefer=("positions", "holdings", "list")):
            if not isinstance(r, dict):
                continue
            code = normalize_code(pick(r, "code", "symbol", "stock_code"))
            if not code:
                continue
            out.append({
                "code": code,
                "name": pick(r, "name", "stock_name"),
                "quantity": to_int(pick(r, "quantity", "volume", "hold", "position", "持仓数量", "amount", "holdQuantity")) or 0,
                "available": to_int(pick(r, "available", "can_sell", "enable", "可用数量", "availableQuantity", "sellable")),
                "cost": to_float(pick(r, "cost", "cost_price", "avg_cost", "成本价", "costPrice")),
                "price": to_float(pick(r, "price", "current_price", "last", "现价", "currentPrice")),
                "market_value": to_float(pick(r, "market_value", "marketValue", "市值")),
                "pnl": to_float(pick(r, "profit", "pnl", "浮动盈亏", "floating_profit")),
                "pnl_pct": to_float(pick(r, "profit_rate", "pnl_pct", "profit_pct", "盈亏比例", "profitRate")),
                "raw": r,
            })
        return out

    async def place(self, code: str, direction: str, price: float, quantity: int) -> dict:
        data = await self.session.call(
            "portfolio_paper_trade", {"code": code, "direction": direction, "price": round(price, 2), "quantity": quantity}
        )
        src = data if isinstance(data, dict) else {"data": data}
        order_id = pick(src, "order_id", "orderId", "id", "委托编号", "entrust_no")
        if order_id is None:
            rec = find_records(src)
            if rec and isinstance(rec[0], dict):
                order_id = pick(rec[0], "order_id", "orderId", "id")
        return {"order_id": str(order_id) if order_id is not None else None, "status": _norm_status(pick(src, "status", "state")), "raw": src}

    async def cancel(self, order_id: str) -> dict:
        data = await self.session.call("portfolio_paper_cancel", {"order_id": order_id})
        return {"ok": True, "raw": data}

    async def history(self, range_: str = "today", month: str | None = None) -> list[dict]:
        args = {"range": range_, "limit": 200}
        if range_ == "history" and month:
            args["month"] = month
        data = await self.session.call("portfolio_paper_history", args)
        out = []
        for r in find_records(data, prefer=("orders", "records", "list", "history")):
            if not isinstance(r, dict):
                continue
            qty = to_int(pick(r, "quantity", "volume", "委托数量", "order_quantity", "entrust_amount")) or 0
            filled = to_int(pick(r, "filled_qty", "filled", "deal_quantity", "成交数量", "dealQuantity", "business_amount"))
            out.append({
                "order_id": str(pick(r, "order_id", "orderId", "id", "委托编号", default="")) or None,
                "code": normalize_code(pick(r, "code", "symbol", "stock_code")),
                "name": pick(r, "name", "stock_name"),
                "direction": _norm_direction(pick(r, "direction", "side", "bs", "买卖方向", "type")),
                "price": to_float(pick(r, "price", "order_price", "委托价格", "entrust_price")),
                "quantity": qty,
                "filled_qty": filled or 0,
                "filled_price": to_float(pick(r, "deal_price", "filled_price", "avg_price", "成交价格", "dealPrice", "business_price")),
                "status": _norm_status(pick(r, "status", "state", "委托状态", "status_name"), filled, qty),
                "time": pick(r, "filledAt", "time", "order_time", "create_time", "createdAt", "委托时间", "datetime"),
                "raw": r,
            })
        return out

    async def profit(self) -> dict:
        data = await self.session.call("portfolio_paper_profit", {})
        return data if isinstance(data, dict) else {"data": data}


class LocalSimGateway(SimTradeGateway):
    """仅在 mock 模式下使用：按合成行情即时撮合，便于联调界面。"""

    INITIAL_CASH = 1_000_000.0

    def _state(self, db) -> dict:
        st = db.get(AppSyncState, "local_sim_account")
        if st is None:
            st = AppSyncState(key="local_sim_account", value={"cash": self.INITIAL_CASH, "initial": self.INITIAL_CASH, "seq": 0})
            db.add(st)
            db.flush()
        return dict(st.value)

    def _save_state(self, db, value: dict) -> None:
        st = db.get(AppSyncState, "local_sim_account")
        st.value = value

    def _holdings(self, db) -> dict[str, dict]:
        rows = db.execute(select(SimOrder).where(SimOrder.status == "filled").order_by(SimOrder.id)).scalars().all()
        h: dict[str, dict] = {}
        today = date.today()
        for o in rows:
            x = h.setdefault(o.code, {"qty": 0, "cost_total": 0.0, "today_buy": 0, "name": o.name})
            px = o.filled_price or o.price
            if o.direction == "buy":
                x["qty"] += o.filled_qty
                x["cost_total"] += o.filled_qty * px
                if o.created_at.date() == today:
                    x["today_buy"] += o.filled_qty
            else:
                if x["qty"] > 0:
                    x["cost_total"] -= x["cost_total"] / x["qty"] * o.filled_qty
                x["qty"] -= o.filled_qty
        return {k: v for k, v in h.items() if v["qty"] > 0}

    async def account(self) -> dict:
        with session_scope() as db:
            st = self._state(db)
            hold = self._holdings(db)
        mv = 0.0
        if hold:
            async with create_provider() as p:
                qs = await p.quotes(list(hold))
            mv = sum(v["qty"] * (qs[c].price if c in qs and qs[c].price else v["cost_total"] / v["qty"]) for c, v in hold.items())
        total = st["cash"] + mv
        return {"total_asset": round(total, 2), "cash": round(st["cash"], 2), "market_value": round(mv, 2),
                "total_pnl": round(total - st["initial"], 2), "raw": {"source": "local"}}

    async def positions(self) -> list[dict]:
        with session_scope() as db:
            hold = self._holdings(db)
        if not hold:
            return []
        async with create_provider() as p:
            qs = await p.quotes(list(hold))
        out = []
        for code, v in hold.items():
            cost = v["cost_total"] / v["qty"]
            price = qs[code].price if code in qs else cost
            out.append({"code": code, "name": v["name"], "quantity": v["qty"], "available": v["qty"] - v["today_buy"],
                        "cost": round(cost, 3), "price": price, "market_value": round(v["qty"] * price, 2),
                        "pnl": round((price - cost) * v["qty"], 2), "pnl_pct": round((price / cost - 1) * 100, 2), "raw": {}})
        return out

    async def place(self, code: str, direction: str, price: float, quantity: int) -> dict:
        async with create_provider() as p:
            q = (await p.quotes([code])).get(code)
        with session_scope() as db:
            st = self._state(db)
            st["seq"] += 1
            order_id = f"L{datetime.now():%Y%m%d}{st['seq']:05d}"
            fill = q is not None and q.price is not None and ((direction == "buy" and price >= q.price) or (direction == "sell" and price <= q.price))
            if direction == "buy" and st["cash"] < price * quantity:
                raise SimTradeError("可用资金不足")
            if direction == "sell":
                hold = self._holdings(db).get(code)
                if not hold or hold["qty"] - hold["today_buy"] < quantity:
                    raise SimTradeError("可卖数量不足（T+1）")
            if fill:
                amount = q.price * quantity
                st["cash"] += -amount if direction == "buy" else amount
            self._save_state(db, st)
        return {"order_id": order_id, "status": "filled" if fill else "pending",
                "filled_price": q.price if fill else None, "raw": {"source": "local"}}

    async def cancel(self, order_id: str) -> dict:
        return {"ok": True, "raw": {"source": "local"}}

    async def history(self, range_: str = "today", month: str | None = None) -> list[dict]:
        return []

    async def profit(self) -> dict:
        return {"source": "local"}


def create_gateway() -> SimTradeGateway:
    if get_settings().data_provider == "mock":
        return LocalSimGateway()
    return TencentMcpSimGateway()


# ---------- 服务函数 ----------


def order_to_dict(o: SimOrder) -> dict:
    return {
        "id": o.id, "order_id": o.order_id, "code": o.code, "name": o.name, "direction": o.direction, "price": o.price,
        "quantity": o.quantity, "status": o.status, "filled_qty": o.filled_qty, "filled_price": o.filled_price,
        "plan_id": o.plan_id, "signal_type": o.signal_type, "signal_name": TYPE_NAMES.get(o.signal_type or "", o.signal_type),
        "source": o.source, "message": o.message, "created_at": o.created_at.isoformat() if o.created_at else None,
        # 从 App 同步来的委托，用腾讯记录的成交时间，而不是同步到本地的时间
        "time": (o.raw or {}).get("filledAt") or (o.created_at.isoformat() if o.created_at else None),
        "updated_at": o.updated_at.isoformat() if o.updated_at else None,
    }


async def account_equity() -> float | None:
    gw = create_gateway()
    try:
        acc = await gw.account()
        return acc.get("total_asset")
    finally:
        await gw.close()


async def default_limit_price(code: str, direction: str) -> float:
    params = get_active_params()
    async with create_provider() as p:
        q = (await p.quotes([code])).get(code)
    if q is None or not q.price:
        raise SimTradeError(f"取不到 {code} 的实时价格")
    off = params["limit_price_offset"]
    px = q.price * (1 + off) if direction == "buy" else q.price * (1 - off)
    with session_scope() as db:
        sb = db.get(StockBasic, code)
        is_st = bool(sb and sb.is_st)
    return clamp_to_limits(px, q.prev_close, code, is_st)


async def place_order(code: str, direction: str, quantity: int, price: float | None = None, plan_id: int | None = None,
                      signal_type: str | None = None, source: str = "manual") -> dict:
    code = normalize_code(code) or code
    if not code.startswith(("sh", "sz")):
        raise SimTradeError("模拟交易仅支持沪深 A 股（北交所不可下单）")
    if direction not in ("buy", "sell"):
        raise SimTradeError("方向必须是 buy 或 sell")
    if quantity <= 0 or quantity % LOT:
        raise SimTradeError("数量必须为 100 的整数倍")
    price = price or await default_limit_price(code, direction)
    gw = create_gateway()
    try:
        if direction == "buy":
            acc = await gw.account()
            if acc.get("cash") is not None and acc["cash"] < price * quantity:
                raise SimTradeError(f"可用资金 {acc['cash']:.2f} 不足以买入 {quantity} 股 @ {price}")
        else:
            pos = {p["code"]: p for p in await gw.positions()}
            avail = pos.get(code, {}).get("available")
            if avail is None:
                avail = pos.get(code, {}).get("quantity", 0)
            if avail < quantity:
                raise SimTradeError(f"可卖数量 {avail} 不足 {quantity}")
        result = await gw.place(code, direction, price, quantity)
    finally:
        await gw.close()
    with session_scope() as db:
        sb = db.get(StockBasic, code)
        order = SimOrder(
            order_id=result.get("order_id"), code=code, name=sb.name if sb else None, direction=direction, price=price,
            quantity=quantity, status=result.get("status", "pending"), plan_id=plan_id, signal_type=signal_type, source=source,
            raw=result.get("raw") if isinstance(result.get("raw"), dict) else None,
        )
        if order.status == "filled":
            order.filled_qty, order.filled_price = quantity, result.get("filled_price") or price
        db.add(order)
        db.flush()
        out = order_to_dict(order)
    if out["status"] == "filled" and plan_id:
        from app.services.position_strategy import apply_fill

        apply_fill(plan_id, direction, quantity, out["filled_price"])
    return out


async def cancel_order(order_id: str) -> dict:
    gw = create_gateway()
    try:
        await gw.cancel(order_id)
    finally:
        await gw.close()
    with session_scope() as db:
        o = db.execute(select(SimOrder).where(SimOrder.order_id == order_id)).scalars().first()
        if o:
            o.status = "cancelled"
    return {"ok": True}


async def sync_orders(range_: str = "today") -> dict:
    """把腾讯侧的委托和成交同步到本地镜像；新成交的订单回写到持仓计划。"""
    gw = create_gateway()
    try:
        remote = await gw.history(range_)
    finally:
        await gw.close()
    from app.services.position_strategy import apply_fill

    new_fills = []
    created = updated = 0
    with session_scope() as db:
        for r in remote:
            if not r.get("order_id"):
                continue
            o = db.execute(select(SimOrder).where(SimOrder.order_id == r["order_id"])).scalars().first()
            if o is None:
                if not r.get("code") or not r.get("direction"):
                    continue
                o = SimOrder(order_id=r["order_id"], code=r["code"], name=r.get("name"), direction=r["direction"],
                             price=r.get("price") or 0, quantity=r.get("quantity") or 0, source="app", status="pending")
                db.add(o)
                created += 1
            prev_status, prev_filled = o.status, o.filled_qty or 0
            o.status = r["status"]
            o.filled_qty = r.get("filled_qty") or (o.quantity if r["status"] == "filled" else prev_filled)
            o.filled_price = r.get("filled_price") or o.filled_price or (o.price if r["status"] == "filled" else None)
            o.raw = r.get("raw")
            if o.filled_qty > prev_filled and o.plan_id:
                new_fills.append((o.plan_id, o.direction, o.filled_qty - prev_filled, o.filled_price or o.price))
            if prev_status != o.status:
                updated += 1
    for plan_id, direction, qty, px in new_fills:
        apply_fill(plan_id, direction, qty, px)
    return {"remote": len(remote), "created": created, "updated": updated, "fills": len(new_fills)}


async def snapshot(snap_date: date | None = None) -> dict:
    snap_date = snap_date or date.today()
    gw = create_gateway()
    try:
        acc = await gw.account()
        pos = await gw.positions()
    finally:
        await gw.close()
    with session_scope() as db:
        stmt = insert(SimAccountDaily).values(snap_date=snap_date, total_asset=acc["total_asset"], cash=acc["cash"],
                                              market_value=acc["market_value"], total_pnl=acc["total_pnl"], raw=acc.get("raw"))
        db.execute(stmt.on_duplicate_key_update(total_asset=stmt.inserted.total_asset, cash=stmt.inserted.cash,
                                                market_value=stmt.inserted.market_value, total_pnl=stmt.inserted.total_pnl,
                                                raw=stmt.inserted.raw))
        for p in pos:
            row = {k: p.get(k) for k in ("code", "name", "quantity", "available", "cost", "price", "market_value", "pnl", "pnl_pct")}
            stmt = insert(SimPositionSnapshot).values(snap_date=snap_date, raw=p.get("raw"), **row)
            db.execute(stmt.on_duplicate_key_update(**{k: stmt.inserted[k] for k in row if k != "code"}))
    return {"account": acc, "positions": len(pos)}


def stats() -> dict:
    with session_scope() as db:
        plans = db.execute(select(PositionPlan).where(PositionPlan.status == "Closed")).scalars().all()
        curve = db.execute(select(SimAccountDaily).order_by(SimAccountDaily.snap_date)).scalars().all()
        order_count = db.scalar(select(func.count()).select_from(SimOrder)) or 0
    by_sig: dict[str, dict] = {}
    for p in plans:
        k = p.entry_signal or "manual"
        x = by_sig.setdefault(k, {"signal": k, "name": TYPE_NAMES.get(k, "手动"), "trades": 0, "wins": 0, "pnl": 0.0, "r_multiples": []})
        x["trades"] += 1
        x["wins"] += 1 if (p.realized_pnl or 0) > 0 else 0
        x["pnl"] += p.realized_pnl or 0
        if p.r_value and p.quantity:
            x["r_multiples"].append((p.realized_pnl or 0) / (p.r_value * p.quantity))
    rows = []
    for x in by_sig.values():
        rms = x.pop("r_multiples")
        rows.append({**x, "win_rate": round(x["wins"] / x["trades"], 3) if x["trades"] else None,
                     "avg_r": round(sum(rms) / len(rms), 2) if rms else None, "pnl": round(x["pnl"], 2)})
    peak, mdd = None, 0.0
    points = []
    for c in curve:
        if c.total_asset is None:
            continue
        peak = c.total_asset if peak is None else max(peak, c.total_asset)
        mdd = max(mdd, (peak - c.total_asset) / peak if peak else 0)
        points.append({"date": c.snap_date.isoformat(), "total_asset": c.total_asset, "cash": c.cash, "market_value": c.market_value})
    wins = [p.realized_pnl for p in plans if (p.realized_pnl or 0) > 0]
    losses = [-p.realized_pnl for p in plans if (p.realized_pnl or 0) < 0]
    return {
        "by_signal": rows,
        "closed_trades": len(plans),
        "win_rate": round(len(wins) / len(plans), 3) if plans else None,
        "profit_factor": round(sum(wins) / sum(losses), 2) if losses else None,
        "max_drawdown": round(mdd, 4),
        "equity_curve": points,
        "order_count": order_count,
    }
