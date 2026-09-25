import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import SimOrder
from app.db.session import session_scope
from app.jobs import watchdog
from app.jobs.pipeline import post_close_pipeline
from app.mcp.errors import McpAuthError
from app.mcp.oauth import oauth_manager
from app.providers import create_provider
from app.services import sim_trade, sync
from app.services.jobs import JobAlreadyRunning, job_label, run_job
from app.services.notify import notify
from app.services.position_strategy import check_intraday_stops

logger = logging.getLogger(__name__)
settings = get_settings()

scheduler = AsyncIOScheduler(timezone=settings.timezone)


def _authorized() -> bool:
    if settings.data_provider == "mock":
        return True
    return bool(oauth_manager.status().get("authorized"))


async def _guarded(name: str, fn) -> None:
    if not _authorized():
        logger.info("跳过定时任务 %s：尚未授权腾讯自选股", name)
        return
    try:
        await run_job(name, fn)
    except JobAlreadyRunning:
        logger.info("任务 %s 仍在运行，本次跳过", name)
    except McpAuthError as exc:
        logger.warning("任务 %s 因授权失效中止：%s", name, exc)
        notify("auth_invalid", "腾讯自选股授权失效", f"定时任务「{job_label(name)}」因此中止。请到设置页重新授权。", "error")
    except Exception:
        logger.exception("定时任务 %s 失败", name)


async def _trading_now() -> bool:
    try:
        async with create_provider() as p:
            return await sync.is_trading_day(p, date.today())
    except Exception:
        return date.today().weekday() < 5


async def job_daily_update() -> None:
    await _guarded("daily_update", sync.daily_update)


async def job_post_close() -> None:
    await _guarded("post_close_pipeline", post_close_pipeline)


async def job_stock_pool() -> None:
    await _guarded("stock_pool", sync.sync_stock_pool)


async def job_scores() -> None:
    if not await _trading_now():
        return
    await _guarded("scores", sync.sync_scores_and_valuations)


async def job_risk_labels() -> None:
    await _guarded("risk_labels", sync.sync_risk_labels)


async def job_fundamentals() -> None:
    await _guarded("fundamentals", sync.sync_fundamentals)


async def job_intraday_stops() -> None:
    if not _authorized() or not await _trading_now():
        return
    try:
        alerts = await check_intraday_stops()
        if alerts:
            logger.info("盘中止损预警：%s", alerts)
        for a in alerts:
            notify("intraday_stop", f"盘中触发止损：{a['code']}", f"现价 {a['price']}，{a['reason']}。请到持仓页确认是否卖出。",
                   "important", key=a["code"])
    except Exception:
        logger.exception("盘中止损检查失败")


async def job_data_check() -> None:
    try:
        await watchdog.check_data_fresh()
    except Exception:
        logger.exception("数据时效检查失败")


async def job_evening_check() -> None:
    try:
        await watchdog.evening_check()
    except Exception:
        logger.exception("流水线与快照检查失败")


async def job_sim_orders() -> None:
    if not _authorized():
        return
    with session_scope() as db:
        pending = db.execute(select(SimOrder.id).where(SimOrder.status.in_(("pending", "partial"))).limit(1)).first()
    if not pending:
        return
    try:
        await sim_trade.sync_orders()
    except Exception:
        logger.exception("同步模拟盘委托失败")


def start_scheduler() -> None:
    weekdays = "mon-fri"
    scheduler.add_job(job_daily_update, CronTrigger(day_of_week=weekdays, hour=15, minute=30), id="daily_update", replace_existing=True)
    scheduler.add_job(job_post_close, CronTrigger(day_of_week=weekdays, hour=16, minute=0), id="post_close", replace_existing=True)
    scheduler.add_job(job_stock_pool, CronTrigger(day_of_week="mon", hour=8, minute=30), id="stock_pool", replace_existing=True)
    # 评分每个交易日同步一次，形成按日的评分快照（约 50 次 MCP 调用）
    scheduler.add_job(job_scores, CronTrigger(day_of_week=weekdays, hour=15, minute=40), id="scores", replace_existing=True)
    # 解禁、减持、停牌等事件变化快，交易日收盘前刷新一次，供当天流水线使用
    scheduler.add_job(job_risk_labels, CronTrigger(day_of_week="mon-fri", hour=15, minute=10), id="risk_labels", replace_existing=True)
    scheduler.add_job(job_fundamentals, CronTrigger(month="4,8,10", day="25-31", hour=18), id="fund_a", replace_existing=True)
    scheduler.add_job(job_fundamentals, CronTrigger(month="5,9,11", day="1-10", hour=18), id="fund_b", replace_existing=True)
    scheduler.add_job(job_intraday_stops, CronTrigger(day_of_week=weekdays, hour="9-14", minute="*/5"), id="intraday_stops", replace_existing=True)
    scheduler.add_job(job_sim_orders, CronTrigger(day_of_week=weekdays, hour="9-15", minute="*"), id="sim_orders", replace_existing=True)
    scheduler.add_job(job_data_check, CronTrigger(day_of_week=weekdays, hour=16, minute=30), id="data_check", replace_existing=True)
    scheduler.add_job(job_evening_check, CronTrigger(day_of_week=weekdays, hour=17, minute=30), id="evening_check", replace_existing=True)
    scheduler.start()
    logger.info("定时任务已启动")


def scheduled_jobs() -> list[dict]:
    return [
        {"id": j.id, "next_run": j.next_run_time.isoformat() if j.next_run_time else None, "trigger": str(j.trigger)}
        for j in scheduler.get_jobs()
    ]
