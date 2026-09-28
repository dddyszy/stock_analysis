"""收盘后流水线：市场环境 → 全市场结构候选 → 持仓建议 → 模拟盘对账 → 写回 App。"""

import asyncio
import logging
from datetime import date

from sqlalchemy import func, select

from app.analysis.market_env import compute_market_env
from app.db.models import KlineDaily
from app.db.session import session_scope
from app.providers import create_provider
from app.services import app_sync, sim_trade
from app.services.jobs import JobContext, running_jobs
from app.services.notify import notify
from app.services.position_strategy import evaluate_all
from app.services.recommend_tracking import update_tracking
from app.services.recommender import latest_run, run_recommendation
from app.services.snapshot import save_status_snapshot
from app.services.sync import is_trading_day

logger = logging.getLogger(__name__)

WAIT_DAILY_UPDATE_MINUTES = 30

STEP_NAMES = {"sim_sync": "同步模拟盘委托", "sim_snapshot": "记录模拟盘快照", "tracking": "更新候选跟踪", "app_group": "写回自选分组"}


async def post_close_pipeline(ctx: JobContext, force: bool = False) -> dict:
    result: dict = {}
    async with create_provider() as provider:
        if not force and not await is_trading_day(provider):
            ctx.update(message="非交易日，跳过", force=True)
            return {"skipped": "non-trading-day"}
        if not force:
            # 日线没更新到当天时，快照会用今天的状态覆盖昨天，扫描也会按旧数据重跑并写回
            for _ in range(WAIT_DAILY_UPDATE_MINUTES):
                if "daily_update" not in running_jobs():
                    break
                ctx.update(message="等待每日增量更新完成", force=True)
                await asyncio.sleep(60)
            with session_scope() as db:
                latest = db.scalar(select(func.max(KlineDaily.trade_date)))
            if latest != date.today():
                notify("pipeline_stale", "收盘后流水线已跳过：日线没有更新到今天",
                       f"最新日线日期 {latest}。请到设置页运行「每日增量更新」，完成后再强制运行收盘后流水线。", "warning")
                ctx.update(message=f"日线最新到 {latest}，跳过", force=True)
                return {"skipped": "stale-kline", "latest": str(latest)}
        ctx.update(message="保存股票状态快照", force=True)
        result["status_snapshot"] = save_status_snapshot()
        ctx.update(message="计算市场环境", force=True)
        env = await compute_market_env(provider)
        result["market_env"] = {"score": env["score"], "regime": env["regime"]}

    result["recommend"] = await run_recommendation(ctx)
    ctx.update(message="评估持仓", force=True)
    result["positions"] = await evaluate_all(ctx)

    for name, step in (("sim_sync", sim_trade.sync_orders), ("sim_snapshot", sim_trade.snapshot)):
        ctx.update(message=STEP_NAMES[name], force=True)
        try:
            result[name] = await step()
        except Exception as exc:
            logger.warning("%s 失败: %s", name, exc)
            result[name] = {"error": str(exc)[:300]}

    ctx.update(message="更新候选跟踪", force=True)
    try:
        result["tracking"] = await update_tracking()
    except Exception as exc:
        logger.warning("更新候选跟踪失败: %s", exc)
        result["tracking"] = {"error": str(exc)[:300]}

    ctx.update(message="写回自选股 App", force=True)
    try:
        run = latest_run()
        codes = [it["code"] for it in run["items"]] if run else []
        result["app_group"] = await app_sync.sync_recommend_group(codes)
    except Exception as exc:
        logger.warning("写回自选分组失败: %s", exc)
        result["app_group"] = {"error": str(exc)[:300]}
    failed = [STEP_NAMES[k] for k, v in result.items() if isinstance(v, dict) and "error" in v and k in STEP_NAMES]
    if failed:
        notify("pipeline_partial", f"收盘后流水线部分步骤失败：{'、'.join(failed)}",
               "；".join(f"{STEP_NAMES[k]}：{v['error']}" for k, v in result.items() if isinstance(v, dict) and "error" in v and k in STEP_NAMES),
               "warning")
    ctx.update(message="收盘后流水线完成" + (f"（{'、'.join(failed)}失败）" if failed else ""), force=True)
    return result
