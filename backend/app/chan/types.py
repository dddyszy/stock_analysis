from dataclasses import asdict, dataclass, field
from datetime import date

UP = 1
DOWN = -1


@dataclass
class ChanConfig:
    bi_mode: str = "new"  # new：新笔；old：老笔
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    divergence_ratio: float = 0.9  # 离开段 MACD 面积小于进入段的该比例才算背驰
    b2_stop_mode: str = "loose"  # loose：二买止损用一买低点；strict：用二买自身低点
    signal_recent_bars: int = 10  # 距今多少根以内的信号算"当前有效"


@dataclass
class MergedBar:
    idx: int
    start: int  # 对应原始 K 线起止下标
    end: int
    high: float
    low: float
    high_raw: int  # 最高价所在原始 K 线下标
    low_raw: int
    direction: int  # 相对前一根合并 K 线的方向


@dataclass
class Fractal:
    kind: str  # top / bottom
    m_idx: int
    raw_idx: int
    price: float
    zone_high: float
    zone_low: float
    dt: date


@dataclass
class Bi:
    idx: int
    direction: int
    start: Fractal
    end: Fractal
    confirmed: bool = True
    macd_area: float = 0.0
    dif_extreme: float = 0.0

    @property
    def high(self) -> float:
        return max(self.start.price, self.end.price)

    @property
    def low(self) -> float:
        return min(self.start.price, self.end.price)

    @property
    def start_raw(self) -> int:
        return self.start.raw_idx

    @property
    def end_raw(self) -> int:
        return self.end.raw_idx

    @property
    def amplitude(self) -> float:
        return self.high - self.low


@dataclass
class Segment:
    idx: int
    direction: int
    bi_start: int
    bi_end: int
    high: float
    low: float
    start_raw: int
    end_raw: int
    start_price: float
    end_price: float
    confirmed: bool = True


@dataclass
class Zhongshu:
    idx: int
    level: str  # bi / seg
    start_move: int
    end_move: int
    zg: float
    zd: float
    gg: float
    dd: float
    start_raw: int
    end_raw: int
    enter_move: int | None
    leave_move: int | None
    finished: bool
    move_count: int

    def overlaps(self, high: float, low: float) -> bool:
        return low <= self.zg and high >= self.zd


@dataclass
class Signal:
    type: str  # B1 B2 B3 S1 S2 S3
    level: str
    raw_idx: int
    dt: date
    price: float
    bi_idx: int
    confirmed: bool
    strength: float
    stop_price: float | None = None
    target1: float | None = None
    target2: float | None = None
    zs_idx: int | None = None
    desc: str = ""
    extra: dict = field(default_factory=dict)
    scope: str = "bi"  # bi：以笔为次级别走势、笔中枢判断；seg：以线段、线段中枢判断

    @property
    def is_buy(self) -> bool:
        return self.type.startswith("B")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["dt"] = self.dt.isoformat()
        return d
