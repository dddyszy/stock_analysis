"""收盘后流水线：市场环境 → 全市场缠论推荐 → 持仓建议 → 模拟盘对账 → 写回 App。"""

import logging

from app.analysis.market_env import compute_market_env
from app.providers import create_provider
from app.services import app_sync, sim_trade
from app.services.jobs import JobContext
from app.services.position_strategy import evaluate_all
from app.services.recommend_tracking import update_tracking
from app.services.recommender import latest_run, run_recommendation
from app.services.sync import is_trading_day

logger = logging.getLogger(__name__)


async def post_close_pipeline(ctx: JobContext, force: bool = False) -> dict:
    result: dict = {}
    async with create_provider() as provider:
        if not force and not await is_trading_day(provider):
            ctx.update(message="非交易日，跳过", force=True)
            return {"skipped": "non-trading-day"}
        ctx.update(message="计算市场环境", force=True)
        env = await compute_market_env(provider)
        result["market_env"] = {"score": env["score"], "regime": env["regime"]}

    result["recommend"] = await run_recommendation(ctx)
    ctx.update(message="评估持仓", force=True)
    result["positions"] = await evaluate_all(ctx)

    for name, step in (("sim_sync", sim_trade.sync_orders), ("sim_snapshot", sim_trade.snapshot)):
        ctx.update(message={"sim_sync": "同步模拟盘委托", "sim_snapshot": "记录模拟盘快照"}[name], force=True)
        try:
            result[name] = await step()
        except Exception as exc:
            logger.warning("%s 失败: %s", name, exc)
            result[name] = {"error": str(exc)[:300]}

    ctx.update(message="更新推荐跟踪", force=True)
    try:
        result["tracking"] = await update_tracking()
    except Exception as exc:
        logger.warning("更新推荐跟踪失败: %s", exc)
        result["tracking"] = {"error": str(exc)[:300]}

    ctx.update(message="写回自选股 App", force=True)
    try:
        run = latest_run()
        codes = [it["code"] for it in run["items"]] if run else []
        result["app_group"] = await app_sync.sync_recommend_group(codes)
    except Exception as exc:
        logger.warning("写回自选分组失败: %s", exc)
        result["app_group"] = {"error": str(exc)[:300]}
    ctx.update(message="收盘后流水线完成", force=True)
    return result
