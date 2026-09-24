from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

INDEX_CODES: dict[str, str] = {
    "sh000001": "上证指数",
    "sh000300": "沪深300",
    "sz399006": "创业板指",
    "sh000852": "中证1000",
}


@dataclass
class Bar:
    dt: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    amount: float | None = None


@dataclass
class StockRef:
    code: str
    name: str
    industry: str | None = None
    industry_code: str | None = None


@dataclass
class Sector:
    code: str
    name: str
    change_pct: float | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Quote:
    code: str
    name: str | None = None
    price: float | None = None
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    amount: float | None = None
    change_pct: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    total_mv: float | None = None
    float_mv: float | None = None
    dt: date | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class FinanceRecord:
    report_date: date
    revenue: float | None = None
    net_profit: float | None = None
    operating_cost: float | None = None
    op_cashflow: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    goodwill: float | None = None
    # 数据源直接给出的比率（百分数）；缺失时由同步服务自行推算
    roe: float | None = None
    revenue_yoy: float | None = None
    profit_yoy: float | None = None
    gross_margin: float | None = None
    debt_ratio: float | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Breadth:
    up: int | None = None
    down: int | None = None
    flat: int | None = None
    limit_up: int | None = None
    limit_down: int | None = None
    amount: float | None = None
    raw: dict = field(default_factory=dict)


class DataProvider(ABC):
    """所有外部数据的统一入口。实现类需要是可在一个 async 上下文中复用的会话。"""

    name: str = "base"

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> "DataProvider":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    @abstractmethod
    async def list_industries(self) -> list[Sector]: ...

    @abstractmethod
    async def sector_constituents(self, sector_code: str) -> list[StockRef]: ...

    @abstractmethod
    async def st_codes(self) -> set[str]: ...

    @abstractmethod
    async def list_date(self, code: str) -> date | None: ...

    @abstractmethod
    async def kline(
        self, code: str, period: str = "day", start: date | None = None, end: date | None = None, fq: str = "qfq"
    ) -> list[Bar]: ...

    @abstractmethod
    async def quotes(self, codes: list[str]) -> dict[str, Quote]: ...

    @abstractmethod
    async def finance(self, code: str, num: int = 8) -> list[FinanceRecord]: ...

    async def finance_batch(self, codes: list[str], num: int = 8) -> dict[str, list[FinanceRecord]]:
        return {c: await self.finance(c, num) for c in codes}

    @abstractmethod
    async def scores(self, codes: list[str]) -> dict[str, dict[str, float | None]]:
        """返回 {code: {comp, funm, risk, cap, tec}}。"""

    @abstractmethod
    async def risks(self, codes: list[str]) -> dict[str, list[str]]: ...

    async def label_codes(self, label: str) -> set[str]:
        """某个标签（如 risk_st、price_below1）下的全部股票代码。"""
        return set()

    async def event_codes(self, event: str) -> set[str]:
        """某个事件（如 shareunlock_next_15）下的全部股票代码。"""
        return set()

    @abstractmethod
    async def breadth(self) -> Breadth: ...

    @abstractmethod
    async def market_overview(self) -> dict: ...

    @abstractmethod
    async def sector_ranking(self, limit: int = 50) -> list[Sector]: ...

    @abstractmethod
    async def trading_days(self, start: date, end: date) -> list[date]: ...
