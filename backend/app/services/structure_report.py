"""个股结构报告：当前结构状态、关键价位、延续 / 例外两种情形，以及同类结构的历史比例。"""

from app.analysis.market_env import INDEX_REGIME_NAMES, latest_index_regime
from app.analysis.structure import classify, scenarios
from app.research.base_rates import HORIZONS, base_rate
from app.services.chan_service import StockAnalysis, analyze_stock


def build_report(code: str, a: StockAnalysis | None = None) -> dict | None:
    a = a or analyze_stock(code)
    if a is None:
        return None
    st = classify(a.day_state or a.day)
    wk = classify(a.week) if a.week is not None else None
    regime = latest_index_regime()
    rates = {h: base_rate(st.key, regime, h) for h in HORIZONS}
    items = []
    for sc in scenarios(st):
        items.append({**sc, "rates": {h: (r["outcomes"].get(sc["kind"]) if r else None) for h, r in rates.items()}})
    notes = []
    last_bi = a.day.bis[-1] if a.day.bis else None
    if last_bi is not None and (not last_bi.confirmed or a.day.summary.get("last_bi_extending")):
        notes.append("最后一笔尚未确认，笔的端点和中枢仍可能随后续走势改写")
    if st.signal and not st.signal["confirmed"]:
        notes.append(f"{st.signal['name']}所在的笔尚未确认，同类信号约有一半会在之后被改写或失效")
    if wk is not None and wk.key != "insufficient":
        notes.append(f"周线处于「{wk.name}」，只用于看大级别位置，不参与判断")
    ref = rates.get(10)
    return {
        "code": code,
        "date": a.day.dates[-1].isoformat() if a.day.dates else None,
        "state": st.to_dict(),
        "weekly_state": wk.to_dict() if wk else None,
        "index_regime": regime, "index_regime_name": INDEX_REGIME_NAMES.get(regime, regime),
        "scenarios": items,
        "undecided": {h: (r["outcomes"].get("undecided") if r else None) for h, r in rates.items()},
        "returns": {h: ({k: r[k] for k in ("n", "mean_ret", "median_ret", "p_positive", "mean_excess", "regime")} if r else None)
                    for h, r in rates.items()},
        "base_rate_meta": (ref or {}).get("meta"),
        "base_rate_at": (ref or {}).get("computed_at"),
        "notes": notes,
        "caveat": "历史比例按「当时能看到的数据」逐日回放统计；同类结构只说明过去出现过的比例，不代表这一次的结果。"
                  "样本取自当前在市的股票，已退市股票不在其中，比例和收益会偏乐观。",
    }
