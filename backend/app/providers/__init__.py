from app.core.config import get_settings
from app.providers.base import DataProvider


def create_provider() -> DataProvider:
    """每次调用返回一个新会话；用 `async with create_provider() as p:` 包住一组调用。"""
    settings = get_settings()
    if settings.data_provider == "mock":
        from app.providers.mock import MockProvider

        return MockProvider()
    if settings.kline_source == "public":
        from app.providers.hybrid import HybridProvider

        return HybridProvider()
    from app.providers.tencent_mcp import TencentMcpProvider

    return TencentMcpProvider()
