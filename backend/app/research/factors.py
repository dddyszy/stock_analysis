"""入场因子：只用信号当天收盘前可见的数据计算，供回测按因子分档检验。

新增因子只需在 FACTORS 里注册一条。计算函数不能访问数据库，也不能读取 ctx.t 之后的 K 线。
"""

import bisect
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from app.services.ashare_rules import volume_lot


@dataclass
class EntryCtx:
    code: str
    bars: list  # 完整序列，只能使用 bars[: t + 1]
    t: int  # 信号当天（收盘可见）的下标
    res: object | None = None  # 截至 t 的缠论结果（窗口），随机组为空
    off: int = 0  # res 中下标相对 bars 的偏移
    signal: object | None = None
    bench: dict[date, float] | None = None  # 中证 1000 收盘
    bench_dates: list[date] | None = None
    ind_index: dict[date, float] | None = None  # 所属行业等权指数
    ind_dates: list[date] | None = None


def _at(series: dict[date, float] | None, dates: list[date] | None, d: date) -> float | None:
    """d 当天或之前最近一天的值。"""
    if not series or not dates:
        return None
    i = bisect.bisect_right(dates, d)
    return series[dates[i - 1]] if i else None


def _ret(ctx: EntryCtx, n: int) -> float | None:
    if ctx.t - n < 0:
        return None
    c0, c1 = ctx.bars[ctx.t - n].close, ctx.bars[ctx.t].close
    return c1 / c0 - 1 if c0 else None


def _series_ret(ctx: EntryCtx, series, dates, n: int) -> float | None:
    if ctx.t - n < 0:
        return None
    v0 = _at(series, dates, ctx.bars[ctx.t - n].dt)
    v1 = _at(series, dates, ctx.bars[ctx.t].dt)
    return v1 / v0 - 1 if v0 and v1 else None


def _diff(a: float | None, b: float | None) -> float | None:
    return a - b if a is not None and b is not None else None


def rs(n: int) -> Callable[[EntryCtx], float | None]:
    return lambda ctx: _diff(_ret(ctx, n), _series_ret(ctx, ctx.bench, ctx.bench_dates, n))


def rs_ind20(ctx: EntryCtx) -> float | None:
    return _diff(_ret(ctx, 20), _series_ret(ctx, ctx.ind_index, ctx.ind_dates, 20))


def ind_rs20(ctx: EntryCtx) -> float | None:
    return _diff(_series_ret(ctx, ctx.ind_index, ctx.ind_dates, 20), _series_ret(ctx, ctx.bench, ctx.bench_dates, 20))


def _mean_vol(ctx: EntryCtx, lo: int, hi: int) -> float | None:
    lo, hi = max(0, lo), min(hi, ctx.t)
    vols = [ctx.bars[i].volume or 0 for i in range(lo, hi + 1)]
    return sum(vols) / len(vols) if vols else None


def _ratio(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def vol_5_60(ctx: EntryCtx) -> float | None:
    if ctx.t < 59:
        return None
    return _ratio(_mean_vol(ctx, ctx.t - 4, ctx.t), _mean_vol(ctx, ctx.t - 59, ctx.t))


def _bi_signal_parts(ctx: EntryCtx):
    s, res = ctx.signal, ctx.res
    if s is None or res is None or getattr(s, "scope", "bi") != "bi":
        return None
    p = s.bi_idx
    if p < 1 or p >= len(res.bis):
        return None
    return res, res.bis[p], res.bis[p - 1]


def breakout_vol(ctx: EntryCtx) -> float | None:
    """三买：离开中枢那一笔的均量 / 中枢区间的均量。"""
    parts = _bi_signal_parts(ctx)
    if parts is None:
        return None
    res, _pull, leave = parts
    zs = next((z for z in res.bi_zhongshus if z.idx == ctx.signal.zs_idx), None)
    if zs is None:
        return None
    off = ctx.off
    return _ratio(_mean_vol(ctx, leave.start_raw + off, leave.end_raw + off), _mean_vol(ctx, zs.start_raw + off, zs.end_raw + off))


def pullback_vol(ctx: EntryCtx) -> float | None:
    """二买、三买：回抽那一笔的均量 / 前一笔上涨的均量。"""
    parts = _bi_signal_parts(ctx)
    if parts is None:
        return None
    _res, pull, prev = parts
    off = ctx.off
    return _ratio(_mean_vol(ctx, pull.start_raw + off, pull.end_raw + off), _mean_vol(ctx, prev.start_raw + off, prev.end_raw + off))


def div_vol(ctx: EntryCtx) -> float | None:
    """一买：背驰段均量 / 进入段均量。"""
    div = (ctx.signal.extra or {}).get("divergence") if ctx.signal is not None else None
    if not div:
        return None
    return _ratio(div.get("leave_vol"), div.get("enter_vol"))


def amount20(ctx: EntryCtx) -> float | None:
    """近 20 日平均成交额（亿元），公开接口历史日线没有成交额，用成交量 × 收盘价估算。"""
    if ctx.t < 19:
        return None
    lot = volume_lot(ctx.code)
    vals = [(b.volume or 0) * b.close * lot for b in ctx.bars[ctx.t - 19 : ctx.t + 1]]
    return sum(vals) / len(vals) / 1e8


def atr_pct(ctx: EntryCtx) -> float | None:
    if ctx.t < 20:
        return None
    trs = []
    for i in range(ctx.t - 19, ctx.t + 1):
        b, prev = ctx.bars[i], ctx.bars[i - 1].close
        trs.append(max(b.high - b.low, abs(b.high - prev), abs(b.low - prev)))
    close = ctx.bars[ctx.t].close
    return sum(trs) / len(trs) / close if close else None


def dist_high250(ctx: EntryCtx) -> float | None:
    lo = max(0, ctx.t - 249)
    high = max(b.high for b in ctx.bars[lo : ctx.t + 1])
    return ctx.bars[ctx.t].close / high - 1 if high else None


@dataclass(frozen=True)
class Factor:
    key: str
    name: str
    fn: Callable[[EntryCtx], float | None]
    applies_to: tuple[str, ...] = ("B1", "B2", "B3")
    for_control: bool = True  # 与信号无关的因子，随机组也计算，用于区分「缠论有用」还是「因子本身有用」
    favored: str | None = None  # 事先假设更好的档位：high / low；None 表示没有方向性假设
    hypothesis: str = ""
    fmt: str = "pct"  # pct：百分比；ratio：倍数；num：数值


FACTORS: list[Factor] = [
    Factor("rs20", "近 20 日相对强弱", rs(20), favored="high", hypothesis="强于大盘的股票出现买点更容易走出来"),
    Factor("rs60", "近 60 日相对强弱", rs(60), favored="high", hypothesis="中期强于大盘的股票出现买点更可靠"),
    Factor("rs_ind20", "近 20 日相对行业强弱", rs_ind20, favored="high", hypothesis="行业内的强者出现买点更可靠"),
    Factor("ind_rs20", "行业近 20 日相对强弱", ind_rs20, favored="high", hypothesis="强势行业里的买点更可靠"),
    Factor("vol_5_60", "近 5 日 / 60 日均量", vol_5_60, favored="low", hypothesis="缩量企稳的买点更可靠", fmt="ratio"),
    Factor("breakout_vol", "突破段量比", breakout_vol, applies_to=("B3",), for_control=False, favored="high",
           hypothesis="放量突破中枢的三买更可靠", fmt="ratio"),
    Factor("pullback_vol", "回抽段量比", pullback_vol, applies_to=("B2", "B3"), for_control=False, favored="low",
           hypothesis="缩量回抽的二买、三买更可靠", fmt="ratio"),
    Factor("div_vol", "背驰段量比", div_vol, applies_to=("B1",), for_control=False, favored="low",
           hypothesis="背驰段缩量的一买更可靠", fmt="ratio"),
    Factor("amount20", "近 20 日日均成交额", amount20, hypothesis="检验买点在不同规模股票上的表现（无方向假设）", fmt="num"),
    Factor("atr_pct", "近 20 日波动率", atr_pct, favored="low", hypothesis="A 股长期存在低波动溢价"),
    Factor("dist_high250", "距 250 日最高价", dist_high250, hypothesis="检验离年内高点远近的影响（无方向假设）"),
]
FACTOR_MAP = {f.key: f for f in FACTORS}


def compute_factors(ctx: EntryCtx, control: bool) -> dict[str, float]:
    out: dict[str, float] = {}
    sig_type = getattr(ctx.signal, "type", None)
    for f in FACTORS:
        if control and not f.for_control:
            continue
        if not control and sig_type not in f.applies_to:
            continue
        try:
            v = f.fn(ctx)
        except (ArithmeticError, IndexError, AttributeError, TypeError):
            v = None
        if v is not None and math.isfinite(v):
            out[f.key] = round(float(v), 5)
    return out
