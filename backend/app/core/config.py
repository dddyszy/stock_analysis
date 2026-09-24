from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "缠论股票分析系统"
    timezone: str = "Asia/Shanghai"

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = "123456"
    db_name: str = "chan_stock"

    # mcp: 腾讯自选股 westock-mcp；mock: 本地合成数据，用于未授权时开发
    data_provider: str = "mcp"
    # public：K 线与行情走腾讯公开接口（MCP 行情类工具限频约每分钟 3~5 次）；mcp：全部走 MCP
    kline_source: str = "public"
    mcp_url: str = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
    mcp_oauth_redirect_uri: str = "http://localhost:8000/api/auth/mcp/callback"
    mcp_oauth_scope: str = "read write"
    mcp_max_concurrency: int = 4
    mcp_rate_per_second: float = 5.0
    mcp_max_retries: int = 3
    mcp_rate_limit_retries: int = 8
    mcp_call_timeout: float = 30.0

    # 纳入分析的交易所；默认只做沪深，北交所没有公开历史 K 线且模拟盘不支持
    exchanges: str = "sh,sz"

    frontend_url: str = "http://localhost:5173"

    scheduler_enabled: bool = True
    backfill_years: int = 5
    quote_batch_size: int = 50
    analysis_daily_bars: int = 1000
    analysis_weekly_bars: int = 400

    app_sync_enabled: bool = True
    app_sync_group_name: str = "缠论推荐"

    @property
    def effective_db_name(self) -> str:
        # 合成数据与真实数据代码会重名，mock 模式必须使用独立的库
        if self.data_provider == "mock":
            return f"{self.db_name}_mock"
        return self.db_name

    @property
    def db_url(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/"
            f"{self.effective_db_name}?charset=utf8mb4"
        )

    @property
    def db_server_url(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/"
            f"?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
