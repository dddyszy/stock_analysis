"""点金术：在低估值、高股息、有规模（改良版再加近三年 ROE）的股票里，股价低于 MA120 的 88% 买入、高于 112% 卖出。

原版来自知乎「奥特之父」：PE < 20、股息率 > 3%、市值 > 50 亿。
改良版：股息率 > 4%，并要求近三年每一年的加权 ROE 都达标（市值 < 500 亿要 > 15%，≥ 500 亿要 > 10%）。
所有数值都来自 analysis/value.py 的 build_series()，只用当天已公告、已除权的数据。
"""

from dataclasses import dataclass

from app.analysis.value import ValueSeries
from app.strategies.base import Criterion, Evaluation

BIG_CAP = 5e10
ZONE_NAMES = {
    "buy": "买入区",
    "near_buy": "接近买入线",
    "middle": "中间区域",
    "sell": "卖出区",
    "insufficient": "数据不足",
}
NEAR_BUY_MARGIN = 1.04  # 距买入线 4% 以内算接近


@dataclass(frozen=True)
class DianjinParams:
    key: str
    name: str
    max_pe: float = 20.0
    min_dy: float = 4.0
    min_mv: float = 5e9
    roe_small: float | None = 15.0  # 市值 < 500 亿时近三年每年 ROE 的门槛；None 表示不看 ROE
    roe_big: float | None = 10.0
    buy_ratio: float = 0.88
    sell_ratio: float = 1.12

    @property
    def rules(self) -> list[str]:
        out = [f"PE（TTM）在 0 到 {self.max_pe:g} 之间", f"股息率（近 12 个月）> {self.min_dy:g}%", f"总市值 > {self.min_mv / 1e8:g} 亿"]
        if self.roe_small is not None:
            out.append(f"近三年每一年的加权 ROE：市值 < 500 亿要 > {self.roe_small:g}%，≥ 500 亿要 > {self.roe_big:g}%")
        out += [f"买入：收盘价 < MA120 × {self.buy_ratio:.0%}", f"卖出：收盘价 > MA120 × {self.sell_ratio:.0%}（持有期间不再要求满足筛选条件）"]
        return out


VARIANTS: dict[str, DianjinParams] = {
    "dianjin_v2": DianjinParams("dianjin_v2", "点金术 · 改良版"),
    "dianjin_v1": DianjinParams("dianjin_v1", "点金术 · 原版", min_dy=3.0, roe_small=None, roe_big=None),
}


def _pct(v: float | None, digits: int = 1) -> str:
    return "--" if v is None else f"{v:.{digits}f}%"


def zone_of(ratio: float | None, p: DianjinParams) -> str:
    if ratio is None:
        return "insufficient"
    if ratio < p.buy_ratio:
        return "buy"
    if ratio > p.sell_ratio:
        return "sell"
    if ratio < p.buy_ratio * NEAR_BUY_MARGIN:
        return "near_buy"
    return "middle"


def evaluate(s: ValueSeries, p: DianjinParams, i: int | None = None) -> Evaluation:
    """对第 i 根 K 线（默认最后一根）判定。"""
    i = len(s.dates) - 1 if i is None else i
    pe, dy, mv, roe3, ma = s.pe[i], s.dy[i], s.mv[i], s.roe3[i], s.ma120[i]
    px = s.adj_close(i)
    ratio = px / ma if ma else None
    crit = [
        Criterion("pe", "市盈率", f"0～{p.max_pe:g}", "亏损" if pe is not None and pe <= 0 else ("--" if pe is None else f"{pe:.1f}"),
                  None if pe is None else 0 < pe < p.max_pe),
        Criterion("dy", "股息率", f"> {p.min_dy:g}%", _pct(dy, 2), None if dy is None else dy > p.min_dy),
        Criterion("mv", "总市值", f"> {p.min_mv / 1e8:g} 亿", "--" if mv is None else f"{mv / 1e8:.0f} 亿", None if mv is None else mv > p.min_mv),
    ]
    if p.roe_small is not None:
        need = p.roe_big if mv is not None and mv >= BIG_CAP else p.roe_small
        crit.append(Criterion(
            "roe", "近三年 ROE", f"每年 > {need:g}%", "不足三年" if roe3 is None else "、".join(f"{r:.1f}%" for r in roe3),
            None if roe3 is None else all(r > need for r in roe3),
        ))
    passed = all(c.passed for c in crit)
    zone = zone_of(ratio, p)
    metrics = {
        "price": round(px, 3), "ma120": round(ma, 3) if ma else None, "ratio": round(ratio, 4) if ratio else None,
        "buy_line": round(ma * p.buy_ratio, 3) if ma else None, "sell_line": round(ma * p.sell_ratio, 3) if ma else None,
        "pe": round(pe, 2) if pe is not None else None, "dy": round(dy, 3) if dy is not None else None,
        "mv": round(mv) if mv is not None else None, "roe3": list(roe3) if roe3 else None,
    }
    failed = [c.name for c in crit if not c.passed]
    if not passed:
        summary = f"未通过筛选：{'、'.join(failed)}"
    elif zone == "buy":
        summary = f"通过筛选，收盘价为 MA120 的 {ratio:.0%}，处于买入区"
    elif zone == "sell":
        summary = f"通过筛选，收盘价为 MA120 的 {ratio:.0%}，处于卖出区"
    elif zone == "insufficient":
        summary = "通过筛选，但日线不足 120 根，无法计算 MA120"
    else:
        summary = f"通过筛选，收盘价为 MA120 的 {ratio:.0%}，距买入线 {px / metrics['buy_line'] - 1:.1%}"
    return Evaluation(p.key, s.code, s.dates[i], passed, zone, ZONE_NAMES[zone], crit, metrics, summary)
