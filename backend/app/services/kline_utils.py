from datetime import date

from app.providers.base import Bar


def week_key(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return iso[0], iso[1]


def resample_weekly(daily: list[Bar]) -> list[Bar]:
    """日线合成周线，周线日期取该周最后一个交易日。"""
    out: list[Bar] = []
    cur: Bar | None = None
    cur_key = None
    for b in daily:
        k = week_key(b.dt)
        if cur is None or k != cur_key:
            if cur is not None:
                out.append(cur)
            cur_key = k
            cur = Bar(dt=b.dt, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume or 0, amount=b.amount or 0)
        else:
            cur.dt = b.dt
            cur.high = max(cur.high, b.high)
            cur.low = min(cur.low, b.low)
            cur.close = b.close
            cur.volume = (cur.volume or 0) + (b.volume or 0)
            cur.amount = (cur.amount or 0) + (b.amount or 0)
    if cur is not None:
        out.append(cur)
    return out
