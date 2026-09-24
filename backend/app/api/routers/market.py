from datetime import date, timedelta

from fastapi import APIRouter
from sqlalchemy import select

from app.analysis.market_env import compute_market_env, env_to_dict, latest_market_env
from app.db.models import KlineDaily, MarketEnvDaily
from app.db.session import session_scope
from app.providers import create_provider
from app.providers.base import INDEX_CODES
from app.services.jobs import start_job
from app.services.sync import load_bars

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/env")
def env() -> dict | None:
    return latest_market_env()


@router.get("/env/history")
def env_history(days: int = 120) -> list[dict]:
    with session_scope() as db:
        rows = db.execute(
            select(MarketEnvDaily).where(MarketEnvDaily.trade_date >= date.today() - timedelta(days=days)).order_by(MarketEnvDaily.trade_date)
        ).scalars().all()
        return [{"trade_date": r.trade_date.isoformat(), "score": r.score, "regime": r.regime, "position_cap": r.position_cap} for r in rows]


@router.post("/env/refresh")
async def refresh() -> dict:
    async def _run(ctx):
        async with create_provider() as p:
            res = await compute_market_env(p)
        return {"score": res["score"], "regime": res["regime"]}

    start_job("market_env", _run)
    return {"started": True}


@router.get("/live")
async def live() -> dict:
    async with create_provider() as p:
        b = await p.breadth()
        ranking = await p.sector_ranking(20)
    return {
        "breadth": {"up": b.up, "down": b.down, "flat": b.flat, "limit_up": b.limit_up, "limit_down": b.limit_down, "amount": b.amount},
        "sectors": [{"code": s.code, "name": s.name, "change_pct": s.change_pct} for s in ranking],
    }


@router.get("/indices/live")
async def indices_live() -> list[dict]:
    """主要指数实时行情，给顶部行情条用。"""
    async with create_provider() as p:
        quotes = await p.quotes(list(INDEX_CODES))
    out = []
    for code, name in INDEX_CODES.items():
        q = quotes.get(code)
        out.append({
            "code": code,
            "name": name,
            "price": q.price if q else None,
            "prev_close": q.prev_close if q else None,
            "change_pct": q.change_pct if q else None,
            "change": round(q.price - q.prev_close, 2) if q and q.price and q.prev_close else None,
            "amount": q.amount if q else None,
            "dt": q.dt.isoformat() if q and q.dt else None,
        })
    return out


@router.get("/indices/series")
def indices_series(days: int = 60) -> dict[str, dict]:
    """主要指数近 N 日收盘价，给迷你走势用。"""
    out = {}
    for code, name in INDEX_CODES.items():
        bars = load_bars(KlineDaily, code, days)
        out[code] = {"name": name, "dates": [b.dt.isoformat() for b in bars], "closes": [b.close for b in bars]}
    return out


@router.get("/env/{trade_date}")
def env_on(trade_date: date) -> dict | None:
    with session_scope() as db:
        row = db.get(MarketEnvDaily, trade_date)
        return env_to_dict(row) if row else None
