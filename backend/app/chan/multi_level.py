"""周线与日线联立：周线定方向和位置，日线找具体买卖点，两者共振时加分。"""

from dataclasses import dataclass, field

from app.chan.analyzer import ChanResult
from app.chan.types import UP, Signal

WEEK_RECENT_BARS = 8


@dataclass
class MultiLevelView:
    week_direction: int
    week_walk_type: str
    week_position: str
    week_uptrend: bool
    week_buy: Signal | None
    week_sell: Signal | None
    resonance: float  # -1 ~ 1
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "week_direction": self.week_direction,
            "week_walk_type": self.week_walk_type,
            "week_position": self.week_position,
            "week_uptrend": self.week_uptrend,
            "week_buy": self.week_buy.to_dict() if self.week_buy else None,
            "week_sell": self.week_sell.to_dict() if self.week_sell else None,
            "resonance": round(self.resonance, 3),
            "notes": self.notes,
        }


def combine(day: ChanResult, week: ChanResult | None) -> MultiLevelView:
    if week is None or not week.bis:
        return MultiLevelView(0, "unknown", "unknown", False, None, None, 0.0, ["周线数据不足"])
    ws = week.summary
    direction = ws.get("last_segment_direction") or ws.get("last_bi_direction") or 0
    walk = ws.get("walk_type", "unknown")
    position = ws.get("position", "unknown")
    recent = week.recent_signals(WEEK_RECENT_BARS)
    week_buy = next((s for s in reversed(recent) if s.is_buy), None)
    week_sell = next((s for s in reversed(recent) if not s.is_buy), None)
    uptrend = walk == "up_trend" or (direction == UP and position == "above")

    score = 0.0
    notes: list[str] = []
    if uptrend:
        score += 0.4
        notes.append("周线处于上涨结构")
    elif walk == "down_trend" and position == "below":
        score -= 0.4
        notes.append("周线处于下跌趋势且在中枢下方")
    if position == "above":
        score += 0.2
        notes.append("价格在周线中枢上方")
    if week_buy is not None and (week_sell is None or week_buy.raw_idx > week_sell.raw_idx):
        score += 0.4
        notes.append(f"周线近期出现{week_buy.type}")
    if week_sell is not None and (week_buy is None or week_sell.raw_idx > week_buy.raw_idx):
        score -= 0.5
        notes.append(f"周线近期出现{week_sell.type}")
    return MultiLevelView(direction, walk, position, uptrend, week_buy, week_sell, max(-1.0, min(1.0, score)), notes)
