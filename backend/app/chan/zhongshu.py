"""中枢：连续三个次级别走势（这里用笔或线段）的重叠区间。

ZG/ZD 由构成中枢的前三段决定，GG/DD 随延伸更新。
中枢在"某一段离开且下一段不回到 [ZD, ZG]"时结束。
"""

from typing import Protocol

from app.chan.types import Zhongshu


class Move(Protocol):
    high: float
    low: float
    start_raw: int
    end_raw: int
    direction: int


def _overlap(m: Move, zd: float, zg: float) -> bool:
    return m.low <= zg and m.high >= zd


def build_zhongshus(moves: list, level: str = "bi") -> list[Zhongshu]:
    out: list[Zhongshu] = []
    n = len(moves)
    i = 1  # 第一段作为进入段
    while i + 2 < n:
        a, b, c = moves[i], moves[i + 1], moves[i + 2]
        zg = min(a.high, b.high, c.high)
        zd = max(a.low, b.low, c.low)
        if zg <= zd:
            i += 1
            continue
        gg = max(a.high, b.high, c.high)
        dd = min(a.low, b.low, c.low)
        end = i + 2
        j = i + 3
        while j < n and _overlap(moves[j], zd, zg):
            if j + 1 < n and not _overlap(moves[j + 1], zd, zg):
                break
            end = j
            gg, dd = max(gg, moves[j].high), min(dd, moves[j].low)
            j += 1
        if j >= n:
            leave, next_i = None, n
        elif _overlap(moves[j], zd, zg):
            leave, next_i = j, j + 1
        else:
            leave, next_i = end, j
        out.append(
            Zhongshu(
                idx=len(out),
                level=level,
                start_move=i,
                end_move=end,
                zg=zg,
                zd=zd,
                gg=gg,
                dd=dd,
                start_raw=moves[i].start_raw,
                end_raw=moves[end].end_raw,
                enter_move=i - 1,
                leave_move=leave,
                finished=leave is not None,
                move_count=end - i + 1,
            )
        )
        if leave is None:
            break
        i = next_i
    return out


def walk_type(zss: list[Zhongshu]) -> str:
    """最近两个中枢不重叠即为趋势，否则为盘整。"""
    if len(zss) < 2:
        return "consolidation" if zss else "unknown"
    a, b = zss[-2], zss[-1]
    if b.zd > a.zg:
        return "up_trend"
    if b.zg < a.zd:
        return "down_trend"
    return "consolidation"
