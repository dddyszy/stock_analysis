from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select

from app.analysis.fundamental import load_views
from app.analysis.market_env import latest_market_env
from app.db.models import FundamentalQuarterly, StockBasic, Watchlist
from app.db.session import session_scope
from app.providers import create_provider
from app.providers.parsing import normalize_code
from app.services.chan_service import analyze_stock
from app.services.position_strategy import build_entry_plan
from app.services.strategy_config import get_active_params

router = APIRouter(prefix="/api", tags=["stocks"])


def _code(code: str) -> str:
    return normalize_code(code) or code


@router.get("/stocks/search")
def search(q: str, limit: int = 20) -> list[dict]:
    like = f"%{q.strip()}%"
    with session_scope() as db:
        rows = db.execute(
            select(StockBasic).where(StockBasic.active.is_(True), or_(StockBasic.code.like(like), StockBasic.name.like(like))).limit(limit)
        ).scalars().all()
        return [{"code": r.code, "name": r.name, "industry": r.industry, "is_index": r.is_index} for r in rows]


@router.get("/stocks/{code}")
def stock_info(code: str) -> dict:
    code = _code(code)
    with session_scope() as db:
        sb = db.get(StockBasic, code)
        if sb is None:
            raise HTTPException(404, f"未找到 {code}")
        basic = {"code": sb.code, "name": sb.name, "exchange": sb.exchange, "industry": sb.industry,
                 "list_date": sb.list_date.isoformat() if sb.list_date else None, "is_st": sb.is_st, "is_index": sb.is_index}
        in_watch = db.get(Watchlist, code) is not None
    fund = load_views([code]).get(code)
    return {"basic": basic, "fundamental": fund.to_dict() if fund else None, "in_watchlist": in_watch}


@router.get("/stocks/{code}/chan")
def stock_chan(code: str, level: str = "day", bars: int = 400) -> dict:
    code = _code(code)
    a = analyze_stock(code)
    if a is None:
        raise HTTPException(404, "K 线数据不足（至少需要 60 根日线）")
    result = a.day if level == "day" else a.week
    if result is None:
        raise HTTPException(404, "周线数据不足")
    payload = result.to_dict(include_bars=True)
    if bars and len(payload["bars"]) > bars:
        cut = payload["bars"][-bars][0]
        payload["bars"] = payload["bars"][-bars:]
        for k in ("dif", "dea", "hist"):
            payload["macd"][k] = payload["macd"][k][-bars:]
        payload["bis"] = [b for b in payload["bis"] if b["end_dt"] >= cut]
        payload["segments"] = [s for s in payload["segments"] if s["end_dt"] >= cut]
        payload["zhongshus"] = [z for z in payload["zhongshus"] if z["end_dt"] >= cut]
        payload["signals"] = [s for s in payload["signals"] if s["dt"] >= cut]
    payload["multi_level"] = a.view.to_dict()

    params = get_active_params()
    env = latest_market_env()
    cap = env["position_cap"] if env else params["regime_caps"]["neutral"]
    entry = None
    recent = a.day.recent_signals(params["signal_recent_bars"], buy_only=True)
    if recent:
        entry = build_entry_plan(a, recent[-1], params["default_equity"], params, cap).to_dict()
    payload["entry_plan"] = entry
    return payload


@router.get("/stocks/{code}/quote")
async def stock_quote(code: str) -> dict:
    code = _code(code)
    async with create_provider() as p:
        q = (await p.quotes([code])).get(code)
    if q is None:
        raise HTTPException(404, "取不到实时行情")
    return {k: v for k, v in q.__dict__.items() if k != "raw"} | {"dt": q.dt.isoformat() if q.dt else None}


@router.get("/stocks/{code}/fundamentals")
def stock_fundamentals(code: str) -> list[dict]:
    code = _code(code)
    with session_scope() as db:
        rows = db.execute(select(FundamentalQuarterly).where(FundamentalQuarterly.code == code).order_by(FundamentalQuarterly.report_date)).scalars().all()
        return [
            {"report_date": r.report_date.isoformat(), "revenue": r.revenue, "net_profit": r.net_profit, "revenue_yoy": r.revenue_yoy,
             "profit_yoy": r.profit_yoy, "gross_margin": r.gross_margin, "roe": r.roe, "debt_ratio": r.debt_ratio,
             "op_cashflow": r.op_cashflow}
            for r in rows
        ]


class WatchItem(BaseModel):
    code: str
    note: str | None = None


@router.get("/watchlist")
def watchlist() -> list[dict]:
    with session_scope() as db:
        rows = db.execute(select(Watchlist, StockBasic).join(StockBasic, StockBasic.code == Watchlist.code, isouter=True).order_by(Watchlist.added_at.desc())).all()
        return [{"code": w.code, "name": s.name if s else None, "industry": s.industry if s else None, "note": w.note,
                 "source": w.source, "added_at": w.added_at.isoformat()} for w, s in rows]


@router.post("/watchlist")
def add_watch(item: WatchItem) -> dict:
    code = _code(item.code)
    with session_scope() as db:
        w = db.get(Watchlist, code) or Watchlist(code=code, source="manual")
        w.note = item.note
        db.merge(w)
    return {"ok": True}


@router.delete("/watchlist/{code}")
def del_watch(code: str) -> dict:
    with session_scope() as db:
        w = db.get(Watchlist, _code(code))
        if w:
            db.delete(w)
    return {"ok": True}
