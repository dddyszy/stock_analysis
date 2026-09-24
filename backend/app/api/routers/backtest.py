from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models import BacktestRun
from app.db.session import session_scope
from app.services.backtest import apply_suggested_weights, run_backtest, run_to_dict
from app.services.jobs import start_job

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    sample_size: int = 50
    codes: list[str] | None = None
    lookback_bars: int = 750
    entry_mode: str = "confirmed"
    seed: int = 42
    split_date: date | None = None
    slippage: float | None = None
    with_control: bool = True


@router.post("")
async def start(req: BacktestRequest) -> dict:
    start_job("backtest", lambda ctx: run_backtest(ctx, req.sample_size, req.codes, req.lookback_bars, req.entry_mode, req.seed,
                                                   req.split_date, req.slippage, req.with_control))
    return {"started": True}


@router.get("/runs")
def runs(limit: int = 20) -> list[dict]:
    with session_scope() as db:
        rows = db.execute(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit)).scalars().all()
        return [run_to_dict(r) for r in rows]


@router.get("/runs/{run_id}")
def run_detail(run_id: int) -> dict:
    with session_scope() as db:
        r = db.get(BacktestRun, run_id)
        if r is None:
            raise HTTPException(404, "回测不存在")
    return run_to_dict(r, with_trades=True)


class ApplyBody(BaseModel):
    activate: bool = False


@router.post("/runs/{run_id}/apply")
def apply(run_id: int, body: ApplyBody) -> dict:
    try:
        return {"config_id": apply_suggested_weights(run_id, body.activate)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
