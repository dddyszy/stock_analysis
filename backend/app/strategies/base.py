"""策略接口：每个策略对单只股票给出逐条条件、所处区域和数值，实盘判定和回测共用同一个判定函数。"""

from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass
class Criterion:
    key: str
    name: str
    rule: str
    value: str
    passed: bool | None  # None 表示数据不足，无法判断


@dataclass
class Evaluation:
    strategy: str
    code: str
    trade_date: date | None
    passed: bool
    zone: str
    zone_name: str
    criteria: list[Criterion] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trade_date"] = self.trade_date.isoformat() if self.trade_date else None
        return d
