"""K 线包含关系处理。"""

from app.chan.types import DOWN, UP, MergedBar


def merge_inclusion(highs: list[float], lows: list[float]) -> list[MergedBar]:
    """相邻 K 线一根完全覆盖另一根时合并：向上时取高高、低高，向下时取低低、高低。
    方向由已合并序列最后两根决定，只有一根时视为向上。"""
    merged: list[MergedBar] = []
    for i, (h, l) in enumerate(zip(highs, lows)):
        if not merged:
            merged.append(MergedBar(0, i, i, h, l, i, i, 0))
            continue
        last = merged[-1]
        included = (h <= last.high and l >= last.low) or (h >= last.high and l <= last.low)
        if not included:
            direction = UP if h > last.high else DOWN
            merged.append(MergedBar(len(merged), i, i, h, l, i, i, direction))
            continue
        if len(merged) >= 2:
            trend = UP if last.high > merged[-2].high else DOWN
        else:
            trend = UP
        if trend == UP:
            new_high, high_raw = (h, i) if h > last.high else (last.high, last.high_raw)
            new_low, low_raw = (l, i) if l > last.low else (last.low, last.low_raw)
        else:
            new_high, high_raw = (h, i) if h < last.high else (last.high, last.high_raw)
            new_low, low_raw = (l, i) if l < last.low else (last.low, last.low_raw)
        last.end = i
        last.high, last.low = new_high, new_low
        last.high_raw, last.low_raw = high_raw, low_raw
    return merged
