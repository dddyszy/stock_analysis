"""结构状态分类与情景判定。

同一个 classify() 同时用于实盘的结构报告和历史基准率统计，保证页面上的「历史比例」统计的正是同一种结构。

每个状态给出一条上方价位和一条下方价位，并说明哪个方向算「延续」：
之后逐日看收盘价，先收在上方价位之上记为「向上」，先收在下方价位之下记为「向下」，都没有就是「未分出」。
"""

from dataclasses import dataclass, field

from app.chan import TYPE_NAMES
from app.chan.analyzer import ChanResult
from app.chan.types import UP

# 结构判定只看最近这么多根日线：实盘报告、每日状态表和基准率回放必须用同一个窗口，否则历史比例统计的不是同一种结构
STATE_BARS = 401

RECENT_SIGNAL_BARS = 10

STATE_NAMES = {
    "B1_pending": "一买待确认", "B1_confirmed": "一买已确认",
    "B2_pending": "二买待确认", "B2_confirmed": "二买已确认",
    "B3_pending": "三买待确认", "B3_confirmed": "三买已确认",
    "above_up": "中枢上方 · 向上离开", "above_pullback": "中枢上方 · 回抽中",
    "inside": "中枢内震荡",
    "below_rebound": "中枢下方 · 反抽中", "below_down": "中枢下方 · 向下离开",
    "insufficient": "结构不足",
}
STATE_ORDER = list(STATE_NAMES)


@dataclass
class StructureState:
    key: str
    upper: float | None = None  # 收盘站上即记为「向上」
    lower: float | None = None  # 收盘跌破即记为「向下」
    continuation: str | None = None  # up / down / both：哪个方向算延续；both 表示两个方向都只是情景
    price: float | None = None
    levels: list[dict] = field(default_factory=list)  # [{name, price, kind}]
    signal: dict | None = None

    @property
    def name(self) -> str:
        return STATE_NAMES.get(self.key, self.key)

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "upper": self.upper, "lower": self.lower,
                "continuation": self.continuation, "price": self.price, "levels": self.levels, "signal": self.signal}


def _r(v: float | None) -> float | None:
    return round(float(v), 3) if v is not None else None


def classify(res: ChanResult | None) -> StructureState:
    if res is None or not res.closes:
        return StructureState("insufficient")
    price = float(res.closes[-1])
    n = len(res.closes)
    zs = res.bi_zhongshus[-1] if res.bi_zhongshus else None
    levels: list[dict] = []
    if zs is not None:
        levels += [{"name": "中枢上沿 ZG", "price": _r(zs.zg), "kind": "zg"}, {"name": "中枢下沿 ZD", "price": _r(zs.zd), "kind": "zd"}]

    # 现价已越过目标一的买点空间已兑现，按相对中枢的位置归类
    buys = [s for s in res.recent_signals(RECENT_SIGNAL_BARS, buy_only=True)
            if s.stop_price is not None and s.target1 is not None and s.stop_price < price < s.target1]
    if buys:
        s = max(buys, key=lambda x: x.raw_idx)
        key = f"{s.type}_{'confirmed' if s.confirmed else 'pending'}"
        levels = [{"name": "认错位", "price": _r(s.stop_price), "kind": "stop"},
                  {"name": "目标一", "price": _r(s.target1), "kind": "target"}] + levels
        return StructureState(key, upper=_r(s.target1), lower=_r(s.stop_price), continuation="up", price=price,
                              levels=levels, signal={"type": s.type, "name": TYPE_NAMES[s.type], "date": s.dt.isoformat(),
                                                     "confirmed": s.confirmed, "price": _r(s.price)})

    if zs is None or len(res.bis) < 3:
        return StructureState("insufficient", price=price, levels=levels)
    last, prev = res.bis[-1], res.bis[-2]
    # 最后一笔之后价格可能已越过前一笔的端点（新的一笔尚未成形），以现价是否突破前一笔端点为准
    if price > zs.zg:
        if last.direction == UP or price >= prev.end.price:
            start = last.start_raw if last.direction == UP else prev.start_raw
            high = float(max(res.highs[start:n]))
            levels.append({"name": "离开段高点", "price": _r(high), "kind": "high"})
            return StructureState("above_up", upper=_r(high), lower=_r(zs.zg), continuation="up", price=price, levels=levels)
        levels.append({"name": "前高", "price": _r(prev.end.price), "kind": "high"})
        return StructureState("above_pullback", upper=_r(prev.end.price), lower=_r(zs.zg), continuation="up", price=price, levels=levels)
    if price < zs.zd:
        if last.direction != UP or price <= prev.end.price:
            start = last.start_raw if last.direction != UP else prev.start_raw
            low = float(min(res.lows[start:n]))
            levels.append({"name": "离开段低点", "price": _r(low), "kind": "low"})
            return StructureState("below_down", upper=_r(zs.zd), lower=_r(low), continuation="down", price=price, levels=levels)
        levels.append({"name": "前低", "price": _r(prev.end.price), "kind": "low"})
        return StructureState("below_rebound", upper=_r(zs.zd), lower=_r(prev.end.price), continuation="down", price=price, levels=levels)
    return StructureState("inside", upper=_r(zs.zg), lower=_r(zs.zd), continuation="both", price=price, levels=levels)


def first_touch(state: StructureState, closes: list[float]) -> str:
    """之后的收盘价序列里，先收在上方价位之上（up）还是先收在下方价位之下（down），都没有返回 none。"""
    for c in closes:
        if state.upper is not None and c >= state.upper:
            return "up"
        if state.lower is not None and c <= state.lower:
            return "down"
    return "none"


def outcome(state: StructureState, touch: str) -> str:
    """把 up / down 映射为 延续 continue / 例外 exception；中枢内震荡分别记为 break_up / break_down。"""
    if touch == "none":
        return "undecided"
    if state.continuation == "both":
        return "break_up" if touch == "up" else "break_down"
    return "continue" if touch == state.continuation else "exception"


def _p(v: float | None) -> str:
    return f"{v:.2f}" if v is not None else "--"


def scenarios(state: StructureState) -> list[dict]:
    """两种情形的文字描述。kind 与 outcome() 的取值一致，用来对上历史比例。"""
    up, lo = _p(state.upper), _p(state.lower)
    k = state.key
    pending = k.endswith("_pending")
    if k.startswith("B3"):
        confirm = "等向上一笔完成后三买确认，" if pending else ""
        return [{"kind": "continue", "title": "延续", "condition": f"收盘不跌破中枢上沿 {lo}，{confirm}向上看目标一 {up}"},
                {"kind": "exception", "title": "例外", "condition": f"收盘跌破 {lo}，三买失效，结构回到中枢震荡"}]
    if k.startswith("B2"):
        return [{"kind": "continue", "title": "延续", "condition": f"不跌破一买低点 {lo}，向上看 {up}"},
                {"kind": "exception", "title": "例外", "condition": f"收盘跌破一买低点 {lo}，二买失效，下跌延续"}]
    if k.startswith("B1"):
        confirm = "等向上一笔完成后一买确认，" if pending else ""
        return [{"kind": "continue", "title": "延续", "condition": f"背驰成立，{confirm}向上回到离开的中枢上沿 {up}"},
                {"kind": "exception", "title": "例外", "condition": f"收盘创新低跌破 {lo}，背驰段延伸，一买被改写"}]
    if k == "above_up":
        return [{"kind": "continue", "title": "延续", "condition": f"继续向上，收盘创出离开段新高 {up}"},
                {"kind": "exception", "title": "例外", "condition": f"回落收盘跌回中枢上沿 {lo} 以下，向上离开失败"}]
    if k == "above_pullback":
        return [{"kind": "continue", "title": "延续", "condition": f"回抽不跌破中枢上沿 {lo}，重新站上前高 {up}（回抽不进中枢可能形成三买）"},
                {"kind": "exception", "title": "例外", "condition": f"收盘跌回中枢上沿 {lo} 以下，向上离开失败，回到中枢震荡"}]
    if k == "inside":
        return [{"kind": "break_up", "title": "向上突破", "condition": f"收盘突破中枢上沿 {up}，之后关注回抽是否形成三买"},
                {"kind": "break_down", "title": "向下跌破", "condition": f"收盘跌破中枢下沿 {lo}，之后关注反抽是否形成三卖"}]
    if k == "below_rebound":
        return [{"kind": "continue", "title": "延续（下跌）", "condition": f"反抽不回中枢，收盘跌破前低 {lo}，下跌延续（可能形成三卖）"},
                {"kind": "exception", "title": "例外", "condition": f"收盘回到中枢下沿 {up} 以上，向下离开失败"}]
    if k == "below_down":
        return [{"kind": "continue", "title": "延续（下跌）", "condition": f"继续下跌，收盘创出离开段新低 {lo}"},
                {"kind": "exception", "title": "例外", "condition": f"反弹收盘回到中枢下沿 {up} 以上，向下离开失败"}]
    return []
