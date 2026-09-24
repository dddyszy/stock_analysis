from fastapi import APIRouter, HTTPException

from app.services.jobs import start_job
from app.services.recommend_tracking import run_perf, tracking_summary, update_tracking
from app.services.recommender import latest_run, list_runs, run_recommendation

router = APIRouter(prefix="/api/recommend", tags=["recommend"])


@router.get("/latest")
def latest() -> dict | None:
    return latest_run()


@router.get("/runs")
def runs(limit: int = 30) -> list[dict]:
    return list_runs(limit)


@router.get("/runs/{run_id}")
def run_detail(run_id: int) -> dict:
    r = latest_run(run_id)
    if r is None:
        raise HTTPException(404, "推荐批次不存在")
    return r


@router.get("/tracking")
def tracking(days: int = 180) -> dict:
    return tracking_summary(days)


@router.get("/runs/{run_id}/perf")
def run_performance(run_id: int) -> list[dict]:
    return run_perf(run_id)


@router.post("/tracking/refresh")
async def refresh_tracking() -> dict:
    start_job("tracking", update_tracking)
    return {"started": True}


@router.post("/run")
async def trigger() -> dict:
    start_job("recommend", run_recommendation)
    return {"started": True}
