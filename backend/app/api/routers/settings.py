from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import JobLog, McpCallLog
from app.db.session import session_scope
from app.jobs.pipeline import post_close_pipeline
from app.jobs.scheduler import scheduled_jobs
from app.mcp.client import limiter_status
from app.providers.tencent_public import public_limiter_status
from app.services import app_sync, strategy_config, sync
from app.services.jobs import cancel_job, register_labels, running_jobs, start_job
from app.services.runtime_state import cooldown_until
from app.services.position_strategy import evaluate_all
from app.services.recommend_tracking import update_tracking
from app.services.recommender import run_recommendation

router = APIRouter(prefix="/api/settings", tags=["settings"])
settings = get_settings()


JOBS = {
    "full_initialize": ("首次初始化（股票池 + 日线回填 + 基本面）", sync.full_initialize),
    "stock_pool": ("刷新股票池", sync.sync_stock_pool),
    "backfill": ("补齐日线（断点续传）", sync.backfill_klines),
    "daily_update": ("每日增量更新", sync.daily_update),
    "scores": ("同步全市场评分与估值", sync.sync_scores_and_valuations),
    "risk_labels": ("同步风险标签与风险事件", sync.sync_risk_labels),
    "fundamentals": ("同步基本面（评分估值 + 候选股财报）", sync.sync_fundamentals),
    "recommend": ("生成推荐", run_recommendation),
    "tracking": ("更新推荐跟踪", update_tracking),
    "evaluate_positions": ("评估持仓", evaluate_all),
    "post_close_pipeline": ("收盘后流水线（强制运行）", lambda ctx: post_close_pipeline(ctx, force=True)),
}


OTHER_LABELS = {"backtest": "策略回测", "backtest_experiment": "出场规则实验", "market_env": "计算市场环境", "mcp_probe": "探测 MCP 工具", "full_initialize": "首次初始化"}
register_labels({**{k: v[0] for k, v in JOBS.items()}, **OTHER_LABELS})


def _job_dict(j: JobLog) -> dict:
    label = JOBS[j.job_name][0] if j.job_name in JOBS else OTHER_LABELS.get(j.job_name, j.job_name)
    return {
        "id": j.id, "job_name": j.job_name, "label": label, "status": j.status,
        "progress": j.progress, "total": j.total, "message": j.message,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }


@router.get("/overview")
def overview() -> dict:
    since = datetime.now() - timedelta(hours=24)
    with session_scope() as db:
        calls = db.execute(
            select(McpCallLog.tool, func.count(), func.sum(func.if_(McpCallLog.ok, 0, 1)), func.avg(McpCallLog.duration_ms))
            .where(McpCallLog.created_at >= since).group_by(McpCallLog.tool)
        ).all()
        today_start = datetime.combine(date.today(), datetime.min.time())
        today_calls = {
            t: (c, int(lim or 0)) for t, c, lim in db.execute(
                select(McpCallLog.tool, func.count(), func.sum(func.if_(McpCallLog.error.like("%限频%"), 1, 0)))
                .where(McpCallLog.created_at >= today_start).group_by(McpCallLog.tool)
            ).all()
        }
        jobs = db.execute(select(JobLog).order_by(JobLog.id.desc()).limit(20)).scalars().all()
        last_errors = db.execute(
            select(McpCallLog).where(McpCallLog.ok.is_(False)).order_by(McpCallLog.id.desc()).limit(10)
        ).scalars().all()
        return {
            "provider": settings.data_provider,
            "database": settings.effective_db_name,
            "sync": sync.sync_overview(),
            "running": running_jobs(),
            "jobs": [_job_dict(j) for j in jobs],
            "available_jobs": [{"name": k, "label": v[0]} for k, v in JOBS.items()],
            "scheduled": scheduled_jobs(),
            "mcp_calls_24h": [
                {"tool": t, "count": c, "errors": int(e or 0), "avg_ms": round(float(a or 0)),
                 "today": today_calls.get(t, (0, 0))[0], "limited_today": today_calls.get(t, (0, 0))[1],
                 "cooldown_until": (u.isoformat(timespec="minutes") if (u := cooldown_until(t)) else None)}
                for t, c, e, a in calls
            ],
            "mcp_recent_errors": [{"tool": x.tool, "error": x.error, "at": x.created_at.isoformat()} for x in last_errors],
            "app_sync": app_sync.status(),
            "limiters": {**limiter_status(), **public_limiter_status()},
        }


@router.get("/jobs")
def jobs(limit: int = 50) -> list[dict]:
    with session_scope() as db:
        rows = db.execute(select(JobLog).order_by(JobLog.id.desc()).limit(limit)).scalars().all()
        return [_job_dict(j) for j in rows]


@router.post("/jobs/{name}")
async def start(name: str) -> dict:
    if name not in JOBS:
        raise HTTPException(404, f"未知任务 {name}")
    start_job(name, JOBS[name][1])
    return {"started": True, "name": name}


@router.post("/jobs/{name}/cancel")
def cancel(name: str) -> dict:
    return {"cancelled": cancel_job(name)}


class ConfigBody(BaseModel):
    name: str | None = None
    params: dict
    note: str | None = None
    activate: bool = False


@router.get("/strategy")
def strategy() -> dict:
    return {"configs": strategy_config.list_configs(), "defaults": strategy_config.DEFAULT_PARAMS}


@router.post("/strategy")
def save_strategy(body: ConfigBody) -> dict:
    if not body.name:
        raise HTTPException(400, "需要配置名称")
    return {"id": strategy_config.save_config(body.name, body.params, body.activate, body.note)}


@router.post("/strategy/{config_id}/activate")
def activate(config_id: int) -> dict:
    strategy_config.activate_config(config_id)
    return {"ok": True}


class AppSyncBody(BaseModel):
    enabled: bool


@router.get("/app-sync")
def app_sync_status() -> dict:
    return {**app_sync.status(), "logs": app_sync.recent_logs(50)}


@router.put("/app-sync")
def set_app_sync(body: AppSyncBody) -> dict:
    app_sync.set_enabled(body.enabled)
    return app_sync.status()


@router.post("/app-sync/run")
async def run_app_sync() -> dict:
    from app.services.recommender import latest_run

    run = latest_run()
    codes = [it["code"] for it in run["items"]] if run else []
    return {"group": await app_sync.sync_recommend_group(codes), "alerts": await app_sync.sync_price_alerts()}
