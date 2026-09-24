"""笔：相邻的有效顶底分型相连。"""

from app.chan.types import DOWN, UP, Bi, Fractal


def _is_valid_pair(start: Fractal, end: Fractal, mode: str) -> bool:
    if start.kind == end.kind:
        return False
    m_gap = end.m_idx - start.m_idx
    if mode == "old":
        if m_gap < 4:
            return False
    else:
        # 新笔：两个分型不共用 K 线，且顶底之间（含）至少 5 根原始 K 线
        if m_gap < 3 or end.raw_idx - start.raw_idx < 4:
            return False
    if start.kind == "bottom":
        return end.price > start.zone_high
    return end.price < start.zone_low


def _more_extreme(a: Fractal, b: Fractal) -> bool:
    """a 是否比同类型的 b 更极端。"""
    return a.price > b.price if a.kind == "top" else a.price < b.price


def build_bis(fractals: list[Fractal], mode: str = "new") -> list[Bi]:
    points: list[Fractal] = []
    for f in fractals:
        if not points:
            points.append(f)
            continue
        last = points[-1]
        if f.kind == last.kind:
            if _more_extreme(f, last):
                points[-1] = f
            continue
        if _is_valid_pair(last, f, mode):
            points.append(f)
            continue
        # 反向分型距离不够，但已经突破上一笔的起点：上一笔作废，起点延伸到 f
        if len(points) >= 2 and _more_extreme(f, points[-2]):
            points.pop()
            points[-1] = f

    bis: list[Bi] = []
    for i in range(1, len(points)):
        s, e = points[i - 1], points[i]
        bis.append(Bi(idx=i - 1, direction=UP if s.kind == "bottom" else DOWN, start=s, end=e))
    if bis:
        bis[-1].confirmed = False
    return bis
