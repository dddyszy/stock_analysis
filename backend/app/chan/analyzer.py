"""单级别缠论分析入口：K 线 → 包含 → 分型 → 笔 → 线段 → 中枢 → 背驰 → 买卖点。"""

from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np

from app.chan.bi import build_bis
from app.chan.divergence import macd, move_strength
from app.chan.fractal import find_fractals
from app.chan.kline import merge_inclusion
from app.chan.segment import build_segments
from app.chan.signals import TYPE_NAMES, detect_signals
from app.chan.types import UP, Bi, ChanConfig, Fractal, Segment, Signal, Zhongshu
from app.chan.zhongshu import build_zhongshus, walk_type


@dataclass
class ChanResult:
    level: str
    dates: list[date]
    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    volumes: list[float]
    fractals: list[Fractal]
    bis: list[Bi]
    segments: list[Segment]
    bi_zhongshus: list[Zhongshu]
    seg_zhongshus: list[Zhongshu]
    dif: np.ndarray
    dea: np.ndarray
    hist: np.ndarray
    signals: list[Signal]
    summary: dict = field(default_factory=dict)

    @property
    def last_close(self) -> float | None:
        return self.closes[-1] if self.closes else None

    def recent_signals(self, bars: int, buy_only: bool = False, valid_only: bool = True) -> list[Signal]:
        n = len(self.dates)
        out = []
        for s in self.signals:
            if n - 1 - s.raw_idx > bars:
                continue
            if buy_only and not s.is_buy:
                continue
            if valid_only and s.extra.get("invalidated"):
                continue
            out.append(s)
        return out

    def to_dict(self, include_bars: bool = True) -> dict:
        def d(i: int) -> str:
            return self.dates[i].isoformat()

        out = {
            "level": self.level,
            "bis": [
                {
                    "idx": b.idx,
                    "direction": b.direction,
                    "start_dt": d(b.start_raw),
                    "start_price": b.start.price,
                    "end_dt": d(b.end_raw),
                    "end_price": b.end.price,
                    "confirmed": b.confirmed,
                    "macd_area": round(b.macd_area, 4),
                }
                for b in self.bis
            ],
            "segments": [
                {
                    "idx": s.idx,
                    "direction": s.direction,
                    "start_dt": d(s.start_raw),
                    "start_price": s.start_price,
                    "end_dt": d(s.end_raw),
                    "end_price": s.end_price,
                    "confirmed": s.confirmed,
                }
                for s in self.segments
            ],
            "zhongshus": [
                {
                    **{k: v for k, v in asdict(z).items() if k not in ("start_raw", "end_raw")},
                    "start_dt": d(z.start_raw),
                    "end_dt": d(z.end_raw),
                }
                for z in self.bi_zhongshus + self.seg_zhongshus
            ],
            "signals": [
                {**s.to_dict(), "name": TYPE_NAMES[s.type]} for s in self.signals
            ],
            "summary": self.summary,
        }
        if include_bars:
            out["bars"] = [
                [d(i), self.opens[i], self.highs[i], self.lows[i], self.closes[i], self.volumes[i]]
                for i in range(len(self.dates))
            ]
            out["macd"] = {
                "dif": [round(float(x), 4) for x in self.dif],
                "dea": [round(float(x), 4) for x in self.dea],
                "hist": [round(float(x), 4) for x in self.hist],
            }
        return out


def _position(price: float | None, zs: Zhongshu | None) -> str:
    if price is None or zs is None:
        return "unknown"
    if price > zs.zg:
        return "above"
    if price < zs.zd:
        return "below"
    return "inside"


def segments_as_moves(segments: list[Segment], dates: list[date]) -> list[Bi]:
    """把线段包装成笔的形式，复用同一套中枢和买卖点算法得到线段级别信号。"""
    moves = []
    for s in segments:
        start_kind, end_kind = ("bottom", "top") if s.direction == UP else ("top", "bottom")
        start = Fractal(start_kind, -1, s.start_raw, s.start_price, s.start_price, s.start_price, dates[s.start_raw])
        end = Fractal(end_kind, -1, s.end_raw, s.end_price, s.end_price, s.end_price, dates[s.end_raw])
        moves.append(Bi(idx=s.idx, direction=s.direction, start=start, end=end, confirmed=s.confirmed))
    return moves


def analyze(bars: list, level: str = "day", cfg: ChanConfig | None = None) -> ChanResult:
    """bars 为具有 dt/open/high/low/close/volume 属性的对象列表，按时间升序。"""
    cfg = cfg or ChanConfig()
    dates = [b.dt for b in bars]
    opens = [float(b.open) for b in bars]
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    closes = [float(b.close) for b in bars]
    volumes = [float(b.volume or 0) for b in bars]

    merged = merge_inclusion(highs, lows)
    fractals = find_fractals(merged, dates)
    bis = build_bis(fractals, cfg.bi_mode)
    dif, dea, hist = macd(closes, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal) if closes else (
        np.array([]), np.array([]), np.array([])
    )
    for b in bis:
        b.macd_area, b.dif_extreme = move_strength(hist, dif, b.start_raw, b.end_raw, b.direction)
    segments = build_segments(bis)
    bi_zss = build_zhongshus(bis, "bi")
    seg_moves = segments_as_moves(segments, dates)
    seg_zss = build_zhongshus(seg_moves, "seg")
    signals = detect_signals(bis, bi_zss, hist, dif, dates, lows, highs, level, cfg, volumes, scope="bi")
    signals += detect_signals(seg_moves, seg_zss, hist, dif, dates, lows, highs, level, cfg, volumes, scope="seg")
    signals.sort(key=lambda s: (s.raw_idx, s.scope, s.type))

    last_bi = bis[-1] if bis else None
    last_zs = bi_zss[-1] if bi_zss else None
    price = closes[-1] if closes else None
    # 最后一笔之后价格已越过笔的终点，说明这一笔还在延伸
    extending = False
    if last_bi is not None and last_bi.end_raw < len(closes) - 1:
        tail = slice(last_bi.end_raw + 1, len(closes))
        if last_bi.direction == UP and max(highs[tail]) > last_bi.end.price:
            extending = True
        if last_bi.direction != UP and min(lows[tail]) < last_bi.end.price:
            extending = True
    summary = {
        "bars": len(bars),
        "last_date": dates[-1].isoformat() if dates else None,
        "last_close": price,
        "bi_count": len(bis),
        "segment_count": len(segments),
        "zhongshu_count": len(bi_zss),
        "walk_type": walk_type(bi_zss),
        "seg_walk_type": walk_type(seg_zss),
        "last_bi_direction": last_bi.direction if last_bi else 0,
        "last_bi_extending": extending,
        "last_segment_direction": segments[-1].direction if segments else 0,
        "position": _position(price, last_zs),
        "last_zhongshu": {"zg": last_zs.zg, "zd": last_zs.zd, "gg": last_zs.gg, "dd": last_zs.dd} if last_zs else None,
        "macd_above_zero": bool(len(dif) and dif[-1] > 0),
    }
    return ChanResult(
        level=level,
        dates=dates,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        fractals=fractals,
        bis=bis,
        segments=segments,
        bi_zhongshus=bi_zss,
        seg_zhongshus=seg_zss,
        dif=dif,
        dea=dea,
        hist=hist,
        signals=signals,
        summary=summary,
    )
