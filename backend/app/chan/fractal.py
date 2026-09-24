"""顶底分型。包含关系处理后，高点和低点同向移动，只需比较中间一根。"""

from datetime import date

from app.chan.types import Fractal, MergedBar


def find_fractals(merged: list[MergedBar], dates: list[date]) -> list[Fractal]:
    out: list[Fractal] = []
    for i in range(1, len(merged) - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]
        # zone 取中间那根 K 线的区间，笔的有效性要求终点越过起点分型中间 K 线的区间
        if b.high > a.high and b.high > c.high:
            out.append(Fractal("top", i, b.high_raw, b.high, b.high, b.low, dates[b.high_raw]))
        elif b.low < a.low and b.low < c.low:
            out.append(Fractal("bottom", i, b.low_raw, b.low, b.high, b.low, dates[b.low_raw]))
    return out
