import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import SimOrder
from app.db.session import session_scope
from app.jobs import watchdog
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


async def job_after_close() -> None:
    from app.jobs.after_close import after_close_chain

    await _guarded("after_close", after_close_chain)


async def job_stock_pool() -> None:
    await _guarded("stock_pool", sync.sync_stock_pool)


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


async def job_base_rates() -> None:
    from app.research.base_rates import run_base_rates

    await _guarded("base_rates", run_base_rates)


async def job_value_finance_nightly() -> None:
    from app.services.value_data import value_finance_nightly

    await _guarded("value_finance_nightly", value_finance_nightly)


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
    # 盘中
    scheduler.add_job(job_sim_orders, CronTrigger(day_of_week=weekdays, hour="9-15", minute="*"), id="sim_orders", replace_existing=True)
    scheduler.add_job(job_intraday_stops, CronTrigger(day_of_week=weekdays, hour="9-14", minute="*/5"), id="intraday_stops", replace_existing=True)
    # 收盘链：风险标签 → 每日增量 → 诊股评分 → 收盘后流水线 → 价值池与策略扫描，按顺序执行
    scheduler.add_job(job_after_close, CronTrigger(day_of_week=weekdays, hour=15, minute=30), id="after_close", replace_existing=True)
    scheduler.add_job(job_evening_check, CronTrigger(day_of_week=weekdays, hour=19, minute=30), id="evening_check", replace_existing=True)
    # 夜间：data_finance 限频最紧，所有财报都放到这里拉
    scheduler.add_job(job_value_finance_nightly, CronTrigger(hour=21, minute=0), id="value_finance_nightly", replace_existing=True)
    # 每周
    scheduler.add_job(job_stock_pool, CronTrigger(day_of_week="mon", hour=8, minute=30), id="stock_pool", replace_existing=True)
    scheduler.add_job(job_base_rates, CronTrigger(day_of_week="sat", hour=12), id="base_rates", replace_existing=True)
    scheduler.start()
    logger.info("定时任务已启动")


def scheduled_jobs() -> list[dict]:
    return [
        {"id": j.id, "next_run": j.next_run_time.isoformat() if j.next_run_time else None, "trigger": str(j.trigger)}
        for j in scheduler.get_jobs()
    ]
