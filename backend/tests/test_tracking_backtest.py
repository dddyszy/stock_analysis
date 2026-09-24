from datetime import date, timedelta

from app.providers.base import Bar
from app.services.backtest import bootstrap_edge, summarize
from app.services.recommend_tracking import ItemRef, compute_perf, spearman

D0 = date(2026, 1, 5)


def _bars(prices: list[tuple[float, float, float, float]], start: date = D0) -> list[Bar]:
    return [Bar(start + timedelta(days=i), o, h, l, c) for i, (o, h, l, c) in enumerate(prices)]


def test_compute_perf_returns_excess_and_first_hit():
    base = [(10, 10, 10, 10)]
    after = [(10.2, 10.5, 10.0, 10.4)] + [(10.4 + i * 0.1, 10.6 + i * 0.1, 10.3 + i * 0.1, 10.5 + i * 0.1) for i in range(19)]
    bars = _bars(base + after)
    bench = _bars([(100, 101, 99, 100)] * 21)[1:]
    perf = compute_perf(ItemRef("sh600000", stop_price=9.5, target1=11.0), bars, {"300": bench, "1000": bench})
    assert perf["entry_price"] == 10.2 and not perf["blocked"]
    assert round(perf["ret5"], 2) == round((bars[5].close / 10.2 - 1) * 100, 2)
    assert perf["ex1000_5"] == perf["ret5"]  # 基准从开盘 100 到收盘 100，收益为 0
    assert perf["first_hit"] == "target" and perf["days"] == 20
    assert perf["ret20"] is not None


def test_compute_perf_one_price_limit_up_is_blocked():
    bars = _bars([(10, 10, 10, 10), (11, 11, 11, 11), (11.5, 12, 11, 11.8)])
    perf = compute_perf(ItemRef("sh600000", 9.5, 12.0), bars, {})
    assert perf["blocked"] and perf["entry_price"] is None


def test_compute_perf_stop_first_and_partial_horizon():
    bars = _bars([(10, 10, 10, 10), (10, 10.1, 9.8, 9.9), (9.9, 10, 9.3, 9.4), (9.4, 9.6, 9.2, 9.5)])
    perf = compute_perf(ItemRef("sz300001", 9.5, 12.0), bars, {})
    assert perf["first_hit"] == "stop" and perf["days"] == 3 and perf["ret5"] is None
    assert perf["max_drawdown"] < 0


def test_spearman():
    assert round(spearman([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]), 6) == 1.0
    assert round(spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]), 6) == -1.0
    assert spearman([1, 2], [1, 2]) is None


def _trade(sig: str, r: float, seg: str, scope: str = "bi") -> dict:
    return {"signal_type": sig, "scope": scope, "r_multiple": r, "pnl_pct": r * 5, "excess": r * 4, "holding_days": 10,
            "exit_reason": "结构止损", "segment": seg}


def test_summarize_split_and_edge():
    trades = [_trade("B1", 1.0, "in") for _ in range(12)] + [_trade("B2", -0.5, "out", "seg") for _ in range(6)]
    control = [_trade("RND", -0.2, "in") for _ in range(20)]
    summary, by_sig, suggested = summarize(trades, control)
    assert summary["in_sample"]["trades"] == 12 and summary["out_sample"]["trades"] == 6
    assert summary["by_scope"]["seg"]["trades"] == 6
    assert summary["control"]["trades"] == 20 and summary["edge_ci"]["edge"] > 0
    assert set(suggested) == {"B1"}  # 只用样本内数据，且每类至少 10 笔


def test_bootstrap_edge_significance():
    good = [1.0, 1.2, 0.8, 1.1, 0.9, 1.0]
    bad = [-0.5, -0.4, -0.6, -0.5, -0.45, -0.55]
    r = bootstrap_edge(good, bad)
    assert r["significant"] and r["ci_low"] > 0
    assert bootstrap_edge([1.0], bad) is None
