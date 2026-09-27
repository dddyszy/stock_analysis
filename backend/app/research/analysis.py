"""因子研究：样本内三分位分档、双重对比、规则逐年滚动检验、多重检验校正与纳入门槛判定。

每条规则是「因子 × 信号类别 × 档位」，例如「近 20 日相对强弱高档的三买」。
"""

from app.chan import TYPE_NAMES
from app.research.factors import FACTORS, Factor
from app.services.backtest import _stat, _year_regime, matched_edge

BUCKETS = ("low", "mid", "high")
BUCKET_NAMES = {"low": "低档", "mid": "中档", "high": "高档"}
GROUP_NAMES = {"ALL": "全部买点", **TYPE_NAMES}

# 纳入选股规则的门槛（v0.5 设计第七节，事先约定）
MIN_TRADES = 200
MIN_POSITIVE_YEARS = 3
FDR = 0.10
MIN_BUCKET_SAMPLE = 30


def _v(t: dict, key: str) -> float | None:
    return ((t.get("tags") or {}).get("f") or {}).get(key)


def _year(t: dict) -> int:
    return t["entry_date"].year


def tertiles(values: list[float]) -> tuple[float, float] | None:
    s = sorted(values)
    if len(s) < MIN_BUCKET_SAMPLE:
        return None
    return s[len(s) // 3], s[2 * len(s) // 3]


def bucket_of(v: float, th: tuple[float, float]) -> str:
    return "low" if v <= th[0] else "mid" if v <= th[1] else "high"


def _thresholds(f: Factor, sig: list[dict], ctrl: list[dict]) -> tuple[float, float] | None:
    pool = sig + (ctrl if f.for_control else [])
    return tertiles([v for t in pool if (v := _v(t, f.key)) is not None])


def _pick(ts: list[dict], f: Factor, th: tuple[float, float], b: str) -> list[dict]:
    return [t for t in ts if (v := _v(t, f.key)) is not None and bucket_of(v, th) == b]


def _ctrl_ref(f: Factor, ctrl: list[dict], th: tuple[float, float], b: str) -> list[dict]:
    """与信号比较的随机组：因子随机组也有值时取同档，否则取全部。"""
    return _pick(ctrl, f, th, b) if f.for_control else ctrl


def _groups(f: Factor) -> list[str]:
    return (["ALL"] if len(f.applies_to) > 1 else []) + list(f.applies_to)


def _in_group(t: dict, g: str) -> bool:
    return g == "ALL" or t["signal_type"] == g


def evaluate_rule(f: Factor, g: str, b: str, sig: list[dict], ctrl: list[dict], full_th: tuple[float, float]) -> dict:
    """规则的逐年滚动检验：每个检验年份只用此前年份确定分档阈值。"""
    sig_g = [t for t in sig if _in_group(t, g)]
    years = sorted({_year(t) for t in sig_g})
    folds, picked, ref = [], [], []
    for y in years[1:]:
        th = _thresholds(f, [t for t in sig if _year(t) < y], [t for t in ctrl if _year(t) < y])
        if th is None:
            continue
        test = _pick([t for t in sig_g if _year(t) == y], f, th, b)
        cref = _ctrl_ref(f, [t for t in ctrl if _year(t) == y], th, b)
        e = matched_edge(test, cref)
        folds.append({"year": y, "trades": len(test), "edge": e["edge"] if e else None})
        picked += test
        ref += cref
    oos_sig = _pick([t for t in sig_g if t.get("segment") == "out"], f, full_th, b)
    oos_ctrl = _ctrl_ref(f, [t for t in ctrl if t.get("segment") == "out"], full_th, b)
    return {
        "folds": folds,
        "overall": matched_edge(picked, ref, _year_regime),
        "oos": matched_edge(oos_sig, oos_ctrl),
        "positive_years": sum(1 for x in folds if x["edge"] is not None and x["edge"] > 0),
    }


def bh_adjust(pvalues: list[float]) -> list[float]:
    """Benjamini-Hochberg 校正后的 q 值。"""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    q = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvalues[i] * m / rank)
        q[i] = min(1.0, running)
    return q


def _criteria(f: Factor, row: dict) -> dict:
    rule, vs = row["rule"], row["vs_control"]
    crit = {
        "trades": row["trades"] >= MIN_TRADES,
        "walk_forward": bool(rule["overall"] and rule["overall"]["ci_low"] > 0),
        "positive_years": rule["positive_years"] >= MIN_POSITIVE_YEARS,
        "out_sample": bool(rule["oos"] and rule["oos"]["edge"] > 0),
        "fdr": bool(vs and vs["edge"] > 0 and row.get("q") is not None and row["q"] < FDR),
        "explained": f.favored == row["bucket"],
    }
    crit["passed"] = all(crit.values())
    return crit


def factor_research(trades: list[dict], control: list[dict]) -> dict:
    ins_sig = [t for t in trades if t.get("segment") == "in"]
    ins_ctrl = [t for t in control if t.get("segment") == "in"]
    factors, rows = [], []
    for f in FACTORS:
        th = _thresholds(f, ins_sig, ins_ctrl)
        entry = {"key": f.key, "name": f.name, "hypothesis": f.hypothesis, "favored": f.favored,
                 "for_control": f.for_control, "fmt": f.fmt, "thresholds": list(th) if th else None, "rows": []}
        factors.append(entry)
        if th is None:
            continue
        for g in _groups(f):
            sig_g = [t for t in trades if _in_group(t, g)]
            for b in BUCKETS:
                sig_b = _pick(sig_g, f, th, b)
                cref = _ctrl_ref(f, control, th, b)
                st = _stat(sig_b)
                row = {
                    "factor": f.key, "group": g, "bucket": b,
                    "name": f"{f.name}{BUCKET_NAMES[b]} · {GROUP_NAMES.get(g, g)}",
                    "trades": st.get("trades", 0), "win_rate": st.get("win_rate"), "avg_r": st.get("avg_r"),
                    "avg_excess": st.get("avg_excess"), "ctrl_trades": len(cref),
                    "vs_control": matched_edge(sig_b, cref),
                    "factor_alone": matched_edge(_pick(control, f, th, b), control) if f.for_control and g == "ALL" else None,
                    "rule": evaluate_rule(f, g, b, trades, control, th),
                }
                entry["rows"].append(row)
                rows.append((f, row))
    tested = [(f, r) for f, r in rows if r["vs_control"]]
    for (_f, r), q in zip(tested, bh_adjust([r["vs_control"]["p"] for _f, r in tested]), strict=True):
        r["q"] = round(q, 4)
    for f, r in rows:
        r["criteria"] = _criteria(f, r)
    ranked = sorted((r for _f, r in rows if r["rule"]["overall"]), key=lambda r: r["rule"]["overall"]["edge"], reverse=True)
    return {
        "factors": factors,
        "tested": len(tested),
        "accepted": [r["name"] for _f, r in rows if r["criteria"]["passed"]],
        "top": [{"name": r["name"], "factor": r["factor"], "group": r["group"], "bucket": r["bucket"], "trades": r["trades"],
                 "wf_edge": r["rule"]["overall"]["edge"], "wf_ci": [r["rule"]["overall"]["ci_low"], r["rule"]["overall"]["ci_high"]],
                 "positive_years": r["rule"]["positive_years"], "q": r.get("q"), "criteria": r["criteria"]} for r in ranked[:15]],
        "thresholds_note": "分档阈值取样本内信号与随机交易合并后的三分位；滚动检验中每年只用此前年份重新确定",
        "criteria_note": f"纳入门槛：≥{MIN_TRADES} 笔、滚动检验显著为正、≥{MIN_POSITIVE_YEARS} 个检验年份为正、样本外为正、"
                         f"BH 校正后 q<{FDR}、档位符合事先假设",
    }

