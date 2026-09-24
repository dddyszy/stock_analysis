"""三类买卖点（以笔为次级别走势、笔中枢为本级别中枢）。

一买：中枢之后向下离开，创出新低，且离开段 MACD 面积小于进入段（背驰）。
      前面有两个依次向下的中枢时为趋势背驰，否则为盘整背驰，力度打折。
二买：一买之后第一次回调不创新低。
三买：向上离开中枢后，回调不回到中枢上沿 ZG 之内。
卖点对称。
"""

from datetime import date

import numpy as np

from app.chan.divergence import compare, move_stats
from app.chan.types import DOWN, UP, Bi, ChanConfig, Signal, Zhongshu
from app.chan.zhongshu import walk_type

TYPE_NAMES = {"B1": "一买", "B2": "二买", "B3": "三买", "S1": "一卖", "S2": "二卖", "S3": "三卖"}


def _zs_formed_before(zss: list[Zhongshu], k: int) -> Zhongshu | None:
    """第 k 笔发生之前已经由三笔构成的最近一个中枢。中枢之后可能因回抽而延伸覆盖 k，
    但买卖点是在 k 结束的当下判断的，所以只看 k 之前的形态。"""
    best = None
    for zs in zss:
        if zs.start_move + 2 < k:
            best = zs
        else:
            break
    return best


def _first_divergence_signal(
    bis: list[Bi], zss: list[Zhongshu], k: int, hist: np.ndarray, dif: np.ndarray, volumes: list[float], cfg: ChanConfig
) -> tuple[Zhongshu, dict, bool, int] | None:
    bi = bis[k]
    zs = _zs_formed_before(zss, k)
    if zs is None or zs.enter_move is None or zs.enter_move < 0:
        return None
    enter = bis[zs.enter_move]
    if enter.direction != bi.direction:
        return None
    core_end = zs.start_move + 2
    # 向前找离开段的起点：从中枢区间内（或另一侧）出发的同向笔
    m = k
    if bi.direction == DOWN:
        while m - 2 > core_end and bis[m].high < zs.zd:
            m -= 2
        if bis[m].high < zs.zd:
            return None
        before_extreme = min(bis[j].low for j in range(zs.start_move, m))
        if bi.low >= before_extreme or bi.low > min(bis[j].low for j in range(m, k + 1)):
            return None
    else:
        while m - 2 > core_end and bis[m].low > zs.zg:
            m -= 2
        if bis[m].low > zs.zg:
            return None
        before_extreme = max(bis[j].high for j in range(zs.start_move, m))
        if bi.high <= before_extreme or bi.high < max(bis[j].high for j in range(m, k + 1)):
            return None
    first_leave = m
    enter_stats = move_stats(hist, dif, volumes, enter.start_raw, enter.end_raw, bi.direction, enter.amplitude)
    leave_amp = abs(bis[first_leave].start.price - bi.end.price)
    leave_stats = move_stats(hist, dif, volumes, bis[first_leave].start_raw, bi.end_raw, bi.direction, leave_amp)
    cmp = compare(enter_stats, leave_stats, bi.direction, cfg.divergence_ratio)
    if not cmp["divergent"]:
        return None
    prior = [z for z in zss if z.idx <= zs.idx]
    wt = walk_type(prior)
    is_trend = wt == ("down_trend" if bi.direction == DOWN else "up_trend")
    return zs, cmp, is_trend, first_leave


def _targets_for_buy(entry: float, stop: float, t1: float | None, t2: float | None) -> tuple[float, float | None]:
    risk = max(entry - stop, entry * 0.01)
    if t1 is None or t1 <= entry:
        t1 = entry + 2 * risk
    if t2 is not None and t2 <= t1:
        t2 = None
    return round(t1, 3), (round(t2, 3) if t2 else None)


def detect_signals(
    bis: list[Bi],
    zss: list[Zhongshu],
    hist: np.ndarray,
    dif: np.ndarray,
    dates: list[date],
    lows: list[float],
    highs: list[float],
    level: str,
    cfg: ChanConfig,
    volumes: list[float] | None = None,
    scope: str = "bi",
) -> list[Signal]:
    """bis 可以是笔，也可以是包装成笔形式的线段（scope="seg"），zss 为对应级别的中枢。"""
    volumes = volumes if volumes is not None else [0.0] * len(lows)
    signals: list[Signal] = []
    first_points: dict[int, Signal] = {}

    # 一买 / 一卖
    for k, bi in enumerate(bis):
        found = _first_divergence_signal(bis, zss, k, hist, dif, volumes, cfg)
        if found is None:
            continue
        zs, cmp, is_trend, first_leave = found
        leave_range = [dates[bis[first_leave].start_raw].isoformat(), dates[bi.end_raw].isoformat()]
        strong_note = "，多指标强背驰" if cmp["strong"] else ""
        strength = cmp["strength"] * (1.0 if is_trend else 0.7)
        kind = "趋势背驰" if is_trend else "盘整背驰"
        enter = bis[zs.enter_move]
        if bi.direction == DOWN:
            entry = bi.low
            t1, t2 = _targets_for_buy(entry, entry, zs.zg, entry + enter.amplitude)
            sig = Signal("B1", level, bi.end_raw, bi.end.dt, entry, k, bi.confirmed, round(strength, 3),
                         stop_price=entry, target1=t1, target2=t2, zs_idx=zs.idx,
                         desc=f"{kind}一买：跌破中枢[{zs.zd:.2f},{zs.zg:.2f}]后力度衰竭{strong_note}",
                         extra={"divergence": cmp, "trend": is_trend, "leave_range": leave_range})
        else:
            sig = Signal("S1", level, bi.end_raw, bi.end.dt, bi.high, k, bi.confirmed, round(strength, 3),
                         zs_idx=zs.idx, desc=f"{kind}一卖：突破中枢[{zs.zd:.2f},{zs.zg:.2f}]后力度衰竭{strong_note}",
                         extra={"divergence": cmp, "trend": is_trend, "leave_range": leave_range})
        # 同一中枢后连续创新低时，只保留最后一个
        for prev_k, prev in list(first_points.items()):
            if prev.zs_idx == zs.idx and prev.type == sig.type:
                first_points.pop(prev_k)
        first_points[k] = sig
    signals.extend(first_points.values())

    # 二买 / 二卖
    for k, s1 in first_points.items():
        p = k + 2
        if p >= len(bis):
            continue
        rebound, pull = bis[k + 1], bis[p]
        if s1.type == "B1" and pull.low > s1.price:
            stop = s1.price if cfg.b2_stop_mode == "loose" else pull.low
            t1, t2 = _targets_for_buy(pull.low, stop, s1.target1 if s1.target1 and s1.target1 > pull.low else rebound.high,
                                      s1.target2)
            signals.append(Signal("B2", level, pull.end_raw, pull.end.dt, pull.low, p, pull.confirmed,
                                  round(0.6 + 0.4 * s1.strength, 3), stop_price=stop, target1=t1, target2=t2,
                                  zs_idx=s1.zs_idx, desc=f"二买：一买 {s1.price:.2f} 之后回调不创新低",
                                  extra={"b1_price": s1.price, "b1_date": s1.dt.isoformat()}))
        elif s1.type == "S1" and pull.high < s1.price:
            signals.append(Signal("S2", level, pull.end_raw, pull.end.dt, pull.high, p, pull.confirmed,
                                  round(0.6 + 0.4 * s1.strength, 3), zs_idx=s1.zs_idx,
                                  desc=f"二卖：一卖 {s1.price:.2f} 之后反弹不创新高",
                                  extra={"s1_price": s1.price, "s1_date": s1.dt.isoformat()}))

    # 三买 / 三卖
    for p in range(2, len(bis)):
        pull, leave = bis[p], bis[p - 1]
        cands = [z for z in zss if p - 3 <= z.end_move <= p - 1]
        if not cands:
            continue
        zs = cands[-1]
        height = max(zs.zg - zs.zd, 1e-9)
        prior = [z for z in zss if z.idx <= zs.idx]
        wt = walk_type(prior)
        if pull.direction == DOWN and leave.high > zs.zg and pull.low > zs.zg and leave.low <= zs.zg:
            dist = (pull.low - zs.zg) / height
            strength = 0.5 + 0.4 * min(1.0, dist) + (0.1 if wt == "up_trend" else 0.0)
            t1, t2 = _targets_for_buy(pull.low, zs.zg, leave.high, pull.low + leave.amplitude)
            signals.append(Signal("B3", level, pull.end_raw, pull.end.dt, pull.low, p, pull.confirmed,
                                  round(min(strength, 1.0), 3), stop_price=zs.zg, target1=t1, target2=t2,
                                  zs_idx=zs.idx, desc=f"三买：离开中枢后回调不回到 ZG {zs.zg:.2f}",
                                  extra={"zg": zs.zg, "zd": zs.zd}))
        elif pull.direction == UP and leave.low < zs.zd and pull.high < zs.zd and leave.high >= zs.zd:
            dist = (zs.zd - pull.high) / height
            strength = 0.5 + 0.4 * min(1.0, dist) + (0.1 if wt == "down_trend" else 0.0)
            signals.append(Signal("S3", level, pull.end_raw, pull.end.dt, pull.high, p, pull.confirmed,
                                  round(min(strength, 1.0), 3), zs_idx=zs.idx,
                                  desc=f"三卖：离开中枢后反弹不回到 ZD {zs.zd:.2f}",
                                  extra={"zg": zs.zg, "zd": zs.zd}))

    # 二买与三买重叠
    b3_bis = {s.bi_idx for s in signals if s.type == "B3"}
    for s in signals:
        if s.type == "B2" and s.bi_idx in b3_bis:
            s.extra["overlap_b3"] = True
            s.desc += "（与三买重叠）"
            s.strength = round(min(1.0, s.strength + 0.1), 3)

    # 信号之后已跌破止损（或突破卖点）视为失效
    n = len(lows)
    for s in signals:
        after = slice(s.raw_idx + 1, n)
        if s.is_buy and s.stop_price is not None and lows[after] and min(lows[after]) < s.stop_price:
            s.extra["invalidated"] = True
        if not s.is_buy and highs[after] and max(highs[after]) > s.price:
            s.extra["invalidated"] = True
    for s in signals:
        s.scope = scope
    signals.sort(key=lambda s: (s.raw_idx, s.type))
    return signals
