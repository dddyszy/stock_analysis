"""MACD 与背驰力度比较。"""

from dataclasses import dataclass

import numpy as np

from app.chan.types import UP


def ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    out = np.empty_like(values, dtype=float)
    if len(values) == 0:
        return out
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    arr = np.asarray(closes, dtype=float)
    dif = ema(arr, fast) - ema(arr, slow)
    dea = ema(dif, signal)
    hist = 2 * (dif - dea)
    return dif, dea, hist


def move_strength(hist: np.ndarray, dif: np.ndarray, start: int, end: int, direction: int) -> tuple[float, float]:
    """一段走势的 MACD 柱面积（只累计与方向一致的柱子）和 DIF 极值。"""
    lo, hi = min(start, end), max(start, end)
    seg_hist = hist[lo : hi + 1]
    seg_dif = dif[lo : hi + 1]
    if len(seg_hist) == 0:
        return 0.0, 0.0
    if direction == UP:
        return float(seg_hist[seg_hist > 0].sum()), float(seg_dif.max())
    return float(-seg_hist[seg_hist < 0].sum()), float(seg_dif.min())


@dataclass
class MoveStats:
    """一段走势的力度指标：MACD 面积、DIF 极值、幅度、K 线数、平均成交量。"""

    area: float
    dif: float
    amplitude: float
    bars: int
    avg_volume: float

    @property
    def slope(self) -> float:
        return self.amplitude / max(self.bars, 1)


def move_stats(hist: np.ndarray, dif: np.ndarray, volumes: list[float], start: int, end: int, direction: int,
               amplitude: float) -> MoveStats:
    area, dif_ext = move_strength(hist, dif, start, end, direction)
    lo, hi = min(start, end), max(start, end)
    vols = volumes[lo : hi + 1]
    avg_vol = float(sum(vols) / len(vols)) if vols else 0.0
    return MoveStats(area, dif_ext, abs(amplitude), hi - lo + 1, avg_vol)


# 力度合成权重：MACD 面积为主，其余三项为辅
WEIGHTS = {"area": 0.5, "dif": 0.2, "slope": 0.15, "volume": 0.15}
STRONG_AGREE = 3


def compare(enter: MoveStats, leave: MoveStats, direction: int, ratio: float) -> dict:
    """离开段相对进入段是否背驰。direction 为离开段方向。

    MACD 面积背驰是必要条件；DIF 不创新高（低）、斜率变缓、成交量萎缩是辅助条件。
    四项中满足 3 项及以上视为强背驰。
    """
    area_div = enter.area > 0 and leave.area < enter.area * ratio
    dif_div = leave.dif < enter.dif if direction == UP else leave.dif > enter.dif
    slope_div = leave.slope < enter.slope
    vol_div = enter.avg_volume > 0 and 0 < leave.avg_volume < enter.avg_volume
    area_strength = max(0.0, min(1.0, 1 - leave.area / enter.area)) if enter.area > 0 else 0.0
    strength = (
        WEIGHTS["area"] * area_strength * 2  # 面积力度 0.5 以上即满分，避免过度依赖单一指标
        + WEIGHTS["dif"] * dif_div
        + WEIGHTS["slope"] * slope_div
        + WEIGHTS["volume"] * vol_div
    )
    agree = int(area_div) + int(dif_div) + int(slope_div) + int(vol_div)
    return {
        "divergent": bool(area_div),
        "area_divergent": bool(area_div),
        "dif_divergent": bool(dif_div),
        "slope_divergent": bool(slope_div),
        "volume_divergent": bool(vol_div),
        "agree": agree,
        "strong": bool(area_div and agree >= STRONG_AGREE),
        "enter_area": round(enter.area, 4),
        "leave_area": round(leave.area, 4),
        "enter_slope": round(enter.slope, 4),
        "leave_slope": round(leave.slope, 4),
        "strength": round(min(1.0, strength), 3),
    }
