"""混合数据源：K 线与行情走腾讯公开接口，其余（股票池、基本面、市场环境等）走 westock-mcp。

北交所在公开接口上没有历史 K 线，公开接口返回为空时回退到 MCP。
"""

import logging
from datetime import date

from app.providers.base import Bar, Quote
from app.providers.tencent_mcp import TencentMcpProvider
from app.providers.tencent_public import TencentPublicQuotes

logger = logging.getLogger(__name__)


class HybridProvider(TencentMcpProvider):
    name = "hybrid"

    def __init__(self) -> None:
        super().__init__()
        self.public = TencentPublicQuotes()

    async def close(self) -> None:
        await self.public.close()
        await super().close()

    async def kline(self, code: str, period: str = "day", start: date | None = None, end: date | None = None,
                    fq: str = "qfq") -> list[Bar]:
        if code.startswith("bj"):
            return await super().kline(code, period, start, end, fq)
        # 出错直接抛出，由同步任务记为失败、下次续传；不退回限频的 MCP，避免卡住所有工作协程
        return await self.public.kline(code, period, start, end, fq)

    async def quotes(self, codes: list[str]) -> dict[str, Quote]:
        try:
            out = await self.public.quotes(codes)
        except Exception as exc:
            logger.warning("公开接口行情失败: %s，改用 MCP", exc)
            out = {}
        missing = [c for c in codes if c not in out]
        if missing and len(missing) < len(codes):
            return out
        if missing:
            out.update(await super().quotes(missing))
        return out
