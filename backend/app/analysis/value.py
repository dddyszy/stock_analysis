"""价值类指标的逐日计算：乘法复权、股息率、PE、市值、近三年 ROE。

每个指标都只用当天已经可见的数据：分红按除权日、财报按公告日生效。
实盘判定和历史回测共用 build_series()，保证两边口径一致。
"""

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.services.ashare_rules import disclosure_deadline

TTM_DAYS = 365
MA_WINDOW = 120

_NUM = r"(\d+(?:\.\d+)?)"


@dataclass
class Event:
    ex_date: date
    cash: float = 0.0  # 每股现金（税前）
    bonus: float = 0.0  # 每股送股
    transfer: float = 0.0  # 每股转增

    @property
    def split(self) -> float:
        return 1.0 + self.bonus + self.transfer


@dataclass
class FinRow:
    report_date: date
    publ_date: date | None
    roe: float | None = None
    roe_weighted: float | None = None
    eps_ttm: float | None = None
    np_ttm: float | None = None

    @property
    def visible_from(self) -> date:
        return self.publ_date or disclosure_deadline(self.report_date)

    @property
    def annual_roe(self) -> float | None:
        return self.roe_weighted if self.roe_weighted is not None else self.roe


@dataclass
class ValueSeries:
    code: str
    dates: list[date]
    open: list[float]
    close: list[float]  # 不复权
    factor: list[float]  # 乘法前复权因子：复权价 = 原价 × factor，最新一天为 1
    div_ps: list[float]  # 近 12 个月每股现金分红（按当天股本折算）
    dy: list[float | None]  # 股息率 %
    pe: list[float | None]
    mv: list[float | None]  # 总市值（元）
    roe3: list[tuple[float, ...] | None]  # 已公告的最近三份年报 ROE，从新到旧
    ma120: list[float | None]  # 复权收盘价的 120 日均线
    events: list[Event] = field(default_factory=list)

    def adj_close(self, i: int) -> float:
        return self.close[i] * self.factor[i]

    def adj_open(self, i: int) -> float:
        return self.open[i] * self.factor[i]

    def index_on(self, d: date) -> int | None:
        """d 当天或之前最近一个交易日的下标。"""
        i = bisect_right(self.dates, d) - 1
        return i if i >= 0 else None


def parse_fh(content: str | None) -> tuple[float, float, float]:
    """「10送3转4派2元」→ 每股（现金，送股，转增）。"""
    if not content:
        return 0.0, 0.0, 0.0
    m = re.search(_NUM, content)
    base = float(m.group(1)) if m else 10.0
    if base <= 0:
        return 0.0, 0.0, 0.0

    def grab(pattern: str) -> float:
        hit = re.search(pattern, content)
        return float(hit.group(1)) / base if hit else 0.0

    return grab("派" + _NUM), grab("送" + _NUM), grab("转(?:增)?" + _NUM)


def _factors(dates: list[date], close: list[float], events: list[Event]) -> list[float]:
    """每次除权的因子 = 除权前收盘价 ÷ 除权参考价；复权因子是当天之后所有除权因子的倒数之积。"""
    step = [1.0] * len(dates)
    for e in events:
        k = bisect_right(dates, e.ex_date - timedelta(days=1)) - 1  # 除权日前最后一个交易日
        if k < 0 or k + 1 >= len(dates):
            continue
        prev = close[k]
        ref = (prev - e.cash) / e.split
        if prev > 0 and ref > 0:
            step[k + 1] *= prev / ref
    out = [1.0] * len(dates)
    acc = 1.0
    for i in range(len(dates) - 1, -1, -1):
        out[i] = acc
        acc /= step[i]
    return out


def _div_ttm(dates: list[date], events: list[Event]) -> list[float]:
    evs = sorted(events, key=lambda e: e.ex_date)
    out = []
    for d in dates:
        total = 0.0
        for j, e in enumerate(evs):
            if not (d - timedelta(days=TTM_DAYS) < e.ex_date <= d) or e.cash <= 0:
                continue
            later = 1.0
            for e2 in evs[j + 1 :]:
                if e2.ex_date <= d:
                    later *= e2.split
            total += e.cash / later
        out.append(total)
    return out


def _fin_on(fin: list[FinRow], d: date) -> tuple[FinRow | None, tuple[float, ...] | None]:
    """d 当天可见的最新一期财报，以及最近三份连续年报的 ROE。"""
    visible = [f for f in fin if f.visible_from <= d]
    if not visible:
        return None, None
    latest = max(visible, key=lambda f: f.report_date)
    annual = sorted((f for f in visible if f.report_date.month == 12), key=lambda f: f.report_date, reverse=True)
    roe3 = None
    if len(annual) >= 3 and all(annual[i].report_date.year - annual[i + 1].report_date.year == 1 for i in range(2)):
        vals = [a.annual_roe for a in annual[:3]]
        if all(v is not None for v in vals):
            roe3 = tuple(vals)
    return latest, roe3


def build_series(code: str, bars: list[tuple[date, float, float]], events: list[Event], fin: list[FinRow]) -> ValueSeries:
    """bars: [(日期, 不复权开盘价, 不复权收盘价)]，按日期升序。"""
    dates = [b[0] for b in bars]
    opens = [b[1] for b in bars]
    close = [b[2] for b in bars]
    factor = _factors(dates, close, events)
    div_ps = _div_ttm(dates, events)
    fin = sorted(fin, key=lambda f: f.visible_from)
    visible_dates = [f.visible_from for f in fin]

    dy: list[float | None] = []
    pe: list[float | None] = []
    mv: list[float | None] = []
    roe3: list[tuple[float, ...] | None] = []
    cache_key = None
    cached: tuple[FinRow | None, tuple[float, ...] | None] = (None, None)
    for i, d in enumerate(dates):
        px = close[i]
        dy.append(div_ps[i] / px * 100 if px > 0 else None)
        n_visible = bisect_right(visible_dates, d)
        if n_visible != cache_key:
            cache_key, cached = n_visible, _fin_on(fin, d)
        latest, r3 = cached
        roe3.append(r3)
        eps = latest.eps_ttm if latest else None
        pe.append(px / eps if eps else None)
        shares = None
        if latest and eps and latest.np_ttm:
            shares = latest.np_ttm / eps
            for e in events:
                if latest.visible_from < e.ex_date <= d:
                    shares *= e.split
        mv.append(px * shares if shares and shares > 0 else None)

    ma: list[float | None] = [None] * len(dates)
    run = 0.0
    adj = [c * f for c, f in zip(close, factor)]
    for i, v in enumerate(adj):
        run += v
        if i >= MA_WINDOW:
            run -= adj[i - MA_WINDOW]
        if i >= MA_WINDOW - 1:
            ma[i] = run / MA_WINDOW
    return ValueSeries(code, dates, opens, close, factor, div_ps, dy, pe, mv, roe3, ma, events=sorted(events, key=lambda e: e.ex_date))


def max_dividend_yield(bars: list[tuple[date, float, float]], events: list[Event]) -> float:
    if not bars:
        return 0.0
    dates = [b[0] for b in bars]
    div = _div_ttm(dates, events)
    return max((dv / b[2] * 100 for dv, b in zip(div, bars) if b[2] > 0), default=0.0)
