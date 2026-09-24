from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class B:
    dt: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 1.0


def zigzag(pivots: list[float], bars_per_leg: int | list[int] = 6, width: float = 0.2, lead: list[float] | None = None) -> list[B]:
    """在相邻拐点之间线性插值生成单调 K 线，拐点处恰好形成分型。"""
    legs = bars_per_leg if isinstance(bars_per_leg, list) else [bars_per_leg] * (len(pivots) - 1)
    prices: list[float] = list(lead or [])
    for i in range(len(pivots) - 1):
        a, b, n = pivots[i], pivots[i + 1], legs[i]
        start = 0 if (i == 0 and not prices) else 1
        for k in range(start, n + 1):
            prices.append(a + (b - a) * k / n)
    d0 = date(2020, 1, 1)
    out = []
    prev = prices[0]
    for i, p in enumerate(prices):
        o = prev
        out.append(B(d0 + timedelta(days=i), o, max(o, p) + width, min(o, p) - width, p))
        prev = p
    return out
