"""复权口径换算：腾讯前复权 → 原价 → 等比前复权。

腾讯的前复权对每次除权按交易所除权公式变换一次：p' = (p − 每股派现) ÷ (1 + 每股送转)。
只有现金分红时等于整体减去一个常数，所以远期的百分比收益、均线比例都会失真；
这里用分红送转事件逆变换回原价，再按「除权前收盘价 ÷ 除权参考价」的比例复权，最新一天等于原价。
"""

from bisect import bisect_right
from dataclasses import replace

from app.analysis.value import Event, _factors
from app.providers.base import Bar


def unadjust_maps(dates: list, events: list[Event]) -> list[tuple[float, float]]:
    """每根 K 线从腾讯前复权价还原原价的线性映射：原价 = a × 前复权价 + b。

    从最近一次除权往前累积：跨过一次更早的除权 e，映射变成 a' = a×(1+送转)，b' = b×(1+送转) + 派现。
    """
    evs = sorted((e for e in events if e.cash or e.bonus or e.transfer), key=lambda e: e.ex_date)
    out = [(1.0, 0.0)] * len(dates)
    a, b = 1.0, 0.0
    j = len(evs) - 1
    for i in range(len(dates) - 1, -1, -1):
        while j >= 0 and evs[j].ex_date > dates[i]:
            e = evs[j]
            a, b = a * e.split, b * e.split + e.cash
            j -= 1
        out[i] = (a, b)
    return out


def to_raw(bars: list[Bar], events: list[Event]) -> list[Bar]:
    maps = unadjust_maps([x.dt for x in bars], events)
    return [
        replace(x, open=a * x.open + b, high=a * x.high + b, low=a * x.low + b, close=a * x.close + b)
        for x, (a, b) in zip(bars, maps)
    ]


def to_ratio_adjusted(bars: list[Bar], events: list[Event]) -> list[Bar]:
    """腾讯前复权 K 线 → 等比前复权 K 线（成交量不变）。没有除权事件时原样返回。"""
    if not bars or not events:
        return list(bars)
    last = bars[-1].dt
    relevant = [e for e in events if bars[0].dt < e.ex_date <= last]
    if not relevant:
        return list(bars)
    raw = to_raw(bars, relevant)
    dates = [x.dt for x in raw]
    factor = _factors(dates, [x.close for x in raw], relevant)
    return [
        replace(x, open=round(x.open * f, 4), high=round(x.high * f, 4), low=round(x.low * f, 4), close=round(x.close * f, 4))
        for x, f in zip(raw, factor)
    ]


def events_between(events: list[Event], start, end) -> list[Event]:
    evs = sorted(events, key=lambda e: e.ex_date)
    lo = bisect_right([e.ex_date for e in evs], start)
    return [e for e in evs[lo:] if e.ex_date <= end]
