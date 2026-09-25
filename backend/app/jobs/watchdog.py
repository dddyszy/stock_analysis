"""健康检查：流水线没跑、日线过期、快照缺失，以及后端停机期间漏跑的流水线。"""

import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select

from app.db.models import JobLog, KlineDaily
from app.db.session import session_scope
from app.providers import create_provider
from app.services import sync
from app.services.notify import notify
from app.services.snapshot import snapshot_health

logger = logging.getLogger(__name__)

PIPELINE_DEADLINE = time(17, 30)  # 交易日这个时间之后还没有成功的流水线就提醒


async def is_trading(d: date) -> bool:
    try:
        async with create_provider() as p:
            return await sync.is_trading_day(p, d)
    except Exception:  # noqa: BLE001
        return d.weekday() < 5


def pipeline_succeeded_on(d: date) -> bool:
    start = datetime.combine(d, time.min)
    with session_scope() as db:
        return bool(db.scalar(
            select(func.count()).select_from(JobLog).where(
                JobLog.job_name == "post_close_pipeline", JobLog.status == "success",
                JobLog.started_at >= start, JobLog.started_at < start + timedelta(days=1),
            )
        ))


async def check_pipeline_ran(d: date | None = None) -> bool:
    d = d or date.today()
    if not await is_trading(d) or pipeline_succeeded_on(d):
        return True
    notify("pipeline_missing", f"{d:%m月%d日}的收盘后流水线没有成功运行",
           "当天的推荐、持仓评估、推荐跟踪和写回 App 都没有执行。可以在设置页点「收盘后流水线（强制运行）」补跑。",
           "error", key=d.isoformat())
    return False


async def check_data_fresh(d: date | None = None) -> bool:
    d = d or date.today()
    if not await is_trading(d):
        return True
    with session_scope() as db:
        latest = db.scalar(select(func.max(KlineDaily.trade_date)))
    if latest is not None and latest >= d:
        return True
    notify("data_stale", "日线数据没有更新到今天",
           f"最新日线日期为 {latest or '无'}。请检查 15:30 的「每日增量更新」任务，或在设置页手动运行。", "error")
    return False


def check_snapshots(d: date | None = None) -> list[str]:
    d = d or date.today()
    health = snapshot_health(d)
    missing = [name for name, key in (("股票状态", "status"), ("风险标签", "labels"), ("估值", "valuation"), ("诊股评分", "scores"))
               if not health[key]]
    if missing:
        notify("snapshot_missing", f"今天的快照缺失：{'、'.join(missing)}",
               "快照用于日后按当时的数据回测。可以在设置页手动运行对应的同步任务补齐。", "warning")
    return missing


async def evening_check() -> None:
    d = date.today()
    await check_pipeline_ran(d)
    if await is_trading(d):
        check_snapshots(d)


async def check_missed_on_startup(now: datetime | None = None) -> date | None:
    """启动时检查最近一个应当运行流水线的交易日；漏跑了就补一条提醒，返回漏跑的日期。"""
    now = now or datetime.now()
    d = now.date() if now.time() >= PIPELINE_DEADLINE else now.date() - timedelta(days=1)
    for _ in range(10):
        if await is_trading(d):
            break
        d -= timedelta(days=1)
    else:
        return None
    if pipeline_succeeded_on(d):
        return None
    await check_pipeline_ran(d)
    return d
