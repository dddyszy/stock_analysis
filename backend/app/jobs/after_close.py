"""收盘链：交易日 15:30 起按顺序执行收盘后的全部步骤。

前一步完成再做下一步，不再靠定时的时间差衔接；某一步失败只记录并通知，后面的步骤照常执行
（流水线开头会检查日线是否已更新到当天，没更新到会自己跳过）。财报统一放到夜间任务，收盘链里不拉。
"""

import logging

from app.jobs.pipeline import post_close_pipeline
from app.mcp.errors import McpAuthError
from app.providers import create_provider
from app.services import sync
from app.services.jobs import JobContext
from app.services.notify import notify
from app.services.value_data import value_daily

logger = logging.getLogger(__name__)

STEPS = (
    ("risk_labels", "风险标签与风险事件", lambda ctx, force: sync.sync_risk_labels(ctx)),
    ("daily_update", "每日增量更新", lambda ctx, force: sync.daily_update(ctx)),
    ("scores", "诊股评分", lambda ctx, force: sync.sync_scores_and_valuations(ctx, with_valuations=False)),
    ("pipeline", "收盘后流水线", lambda ctx, force: post_close_pipeline(ctx, force=force, fetch_finance=False)),
    ("value_daily", "价值池数据与策略扫描", lambda ctx, force: value_daily(ctx)),
)


async def after_close_chain(ctx: JobContext, force: bool = False) -> dict:
    if not force:
        async with create_provider() as provider:
            if not await sync.is_trading_day(provider):
                ctx.update(message="非交易日，跳过", force=True)
                return {"skipped": "non-trading-day"}
    result: dict = {}
    failed: list[str] = []
    for key, name, step in STEPS:
        ctx.update(message=f"收盘链：{name}", force=True)
        try:
            result[key] = await step(ctx, force)
        except McpAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("收盘链步骤 %s 失败", name)
            result[key] = {"error": str(exc)[:300]}
            failed.append(f"{name}：{str(exc)[:120]}")
    if failed:
        notify("after_close_partial", f"收盘链有 {len(failed)} 个步骤失败", "；".join(failed), "warning")
    ctx.update(message="收盘链完成" + (f"（{len(failed)} 步失败）" if failed else ""), force=True)
    return result
