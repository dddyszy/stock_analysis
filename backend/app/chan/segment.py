"""线段：至少三笔，用特征序列的分型判断结束。

向上线段的特征序列是其中的向下笔，按向上方向做包含处理后出现顶分型即线段可能结束：
- 第一种情况：顶分型第一、二元素之间没有缺口，线段在顶点结束；
- 第二种情况：有缺口，需要后面新的向下线段的特征序列（向上笔）出现底分型才确认，
  在此之前如果价格创出新高，原线段延续。
向下线段对称处理。
"""

from dataclasses import dataclass

from app.chan.types import DOWN, UP, Bi, Segment


@dataclass
class _Feature:
    high: float
    low: float
    bi_idx: int


def _included(a: _Feature, b: _Feature) -> bool:
    return (b.high <= a.high and b.low >= a.low) or (b.high >= a.high and b.low <= a.low)


def _merge(a: _Feature, b: _Feature, direction: int) -> _Feature:
    if direction == UP:
        rep = a.bi_idx if a.high >= b.high else b.bi_idx
        return _Feature(max(a.high, b.high), max(a.low, b.low), rep)
    rep = a.bi_idx if a.low <= b.low else b.bi_idx
    return _Feature(min(a.high, b.high), min(a.low, b.low), rep)


def _feature_fractal(feats: list[_Feature], direction: int) -> bool:
    a, b, c = feats[-3], feats[-2], feats[-1]
    if direction == UP:
        return b.high > a.high and b.high > c.high
    return b.low < a.low and b.low < c.low


def _has_gap(feats: list[_Feature], direction: int) -> bool:
    a, b = feats[-3], feats[-2]
    return b.low > a.high if direction == UP else b.high < a.low


def _confirm_after_gap(bis: list[Bi], peak_bi: int, direction: int) -> bool:
    """缺口情况：检查新线段（方向与原线段相反）的特征序列是否出现反向分型。"""
    new_dir = -direction
    peak_price = bis[peak_bi].start.price
    feats: list[_Feature] = []
    k = peak_bi + 1
    while k < len(bis):
        b = bis[k]
        if (direction == UP and b.high > peak_price) or (direction == DOWN and b.low < peak_price):
            return False
        el = _Feature(b.high, b.low, k)
        if feats and _included(feats[-1], el):
            feats[-1] = _merge(feats[-1], el, new_dir)
        else:
            feats.append(el)
        if len(feats) >= 3 and _feature_fractal(feats, new_dir):
            return True
        k += 2
    return False


def _find_end(bis: list[Bi], s: int, direction: int) -> tuple[str, int] | None:
    """返回 ("end", 线段最后一笔下标) 或 ("broken", 跌破起点的那一笔下标) 或 None（未完成）。"""
    start_price = bis[s].start.price
    feats: list[_Feature] = []
    k = s + 1
    while k < len(bis):
        b = bis[k]
        if (direction == UP and b.low < start_price) or (direction == DOWN and b.high > start_price):
            return ("broken", k)
        el = _Feature(b.high, b.low, k)
        if feats and _included(feats[-1], el):
            feats[-1] = _merge(feats[-1], el, direction)
        else:
            feats.append(el)
        if len(feats) >= 3 and _feature_fractal(feats, direction):
            peak_bi = feats[-2].bi_idx
            if not _has_gap(feats, direction) or _confirm_after_gap(bis, peak_bi, direction):
                return ("end", peak_bi - 1)
        k += 2
    return None


def _make(bis: list[Bi], idx: int, s: int, e: int, confirmed: bool) -> Segment:
    part = bis[s : e + 1]
    return Segment(
        idx=idx,
        direction=bis[s].direction,
        bi_start=s,
        bi_end=e,
        high=max(b.high for b in part),
        low=min(b.low for b in part),
        start_raw=bis[s].start_raw,
        end_raw=bis[e].end_raw,
        start_price=bis[s].start.price,
        end_price=bis[e].end.price,
        confirmed=confirmed,
    )


def _extreme_same_dir(bis: list[Bi], s: int, direction: int) -> int:
    best = s
    for k in range(s, len(bis), 2):
        if direction == UP and bis[k].end.price >= bis[best].end.price:
            best = k
        if direction == DOWN and bis[k].end.price <= bis[best].end.price:
            best = k
    return best


def build_segments(bis: list[Bi]) -> list[Segment]:
    segs: list[Segment] = []
    i, n = 0, len(bis)
    while i + 2 < n:
        if not segs:
            b0, b1, b2 = bis[i], bis[i + 1], bis[i + 2]
            if min(b0.high, b1.high, b2.high) <= max(b0.low, b1.low, b2.low):
                i += 1
                continue
        direction = bis[i].direction
        found = _find_end(bis, i, direction)
        if found is None:
            e = _extreme_same_dir(bis, i, direction)
            if e - i >= 2:
                segs.append(_make(bis, len(segs), i, e, False))
            break
        kind, k = found
        if kind == "broken":
            if segs and segs[-1].direction == -direction:
                prev = segs.pop()
                segs.append(_make(bis, len(segs), prev.bi_start, k, True))
                i = k + 1
            else:
                i += 1
            continue
        segs.append(_make(bis, len(segs), i, k, True))
        i = k + 1
    return segs
