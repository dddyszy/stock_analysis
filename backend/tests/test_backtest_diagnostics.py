import random
from datetime import date, timedelta

from app.providers.base import Bar
from app.analysis.market_env import index_regime_map as regime_map
from app.services.backtest import diagnose, matched_edge, walk_forward


def _t(sig: str, regime: str, r: float, year: int = 2024, excess: float = 0.0, **tags) -> dict:
    return {
        "signal_type": sig, "scope": "bi", "entry_date": date(year, 6, 1), "r_multiple": r, "pnl_pct": r * 5,
        "excess": excess, "holding_days": 10, "tags": {"regime": regime, **tags},
    }


def test_matched_edge_removes_market_timing():
    # 信号全部出现在上涨期，随机组上涨期 1R、下跌期 -1R；信号本身在上涨期也只有 1R，择时之外没有优势
    sig = [_t("B1", "up", 1.0) for _ in range(30)]
    ctrl = [_t("RND", "up", 1.0) for _ in range(30)] + [_t("RND", "down", -1.0) for _ in range(30)]
    m = matched_edge(sig, ctrl)
    assert m["edge"] == 0 and m["control_avg_r"] == 1.0 and not m["significant"]


def test_matched_edge_detects_real_edge():
    rng = random.Random(1)
    sig = [_t("B2", "range", 1.0 + rng.uniform(-0.2, 0.2)) for _ in range(40)]
    ctrl = [_t("RND", "range", rng.uniform(-0.5, 0.5)) for _ in range(80)]
    m = matched_edge(sig, ctrl)
    assert m["edge"] > 0.8 and m["significant"] and m["ci_low"] > 0


def test_matched_edge_needs_control_in_same_regime():
    sig = [_t("B1", "down", 1.0) for _ in range(10)]
    ctrl = [_t("RND", "up", 0.0) for _ in range(20)]
    assert matched_edge(sig, ctrl) is None


def test_walk_forward_only_uses_past_years():
    trades, ctrl = [], []
    # 2023 年：B1 下跌期好、B3 上涨期差；2024 年反过来。滚动检验应在 2024 年选中 B1·下跌并暴露失效
    for _ in range(12):
        trades += [_t("B1", "down", 1.0, 2023), _t("B3", "up", -1.0, 2023)]
        trades += [_t("B1", "down", -1.0, 2024), _t("B3", "up", 1.0, 2024)]
        ctrl += [_t("RND", r, 0.0, y) for r in ("up", "down") for y in (2023, 2024)]
    wf = walk_forward(trades, ctrl, min_n=10)
    fold = wf["folds"][0]
    assert fold["year"] == 2024 and fold["selected"] == ["一买 · 下跌"]
    assert fold["test_selected"]["avg_r"] == -1.0 and wf["overall"]["edge"]["edge"] == -1.0


def test_diagnose_dimensions():
    trades = [_t("B1", "up", 1.0, week="up", div="strong") for _ in range(6)] + [_t("B3", "down", -0.5, week="down") for _ in range(6)]
    ctrl = [_t("RND", r, 0.0) for r in ("up", "down") for _ in range(10)]
    dims = {d["key"]: d for d in diagnose(trades, ctrl)}
    assert set(dims) == {"signal_type", "regime", "type_regime", "year", "week", "div", "scope"}
    week = {r["key"]: r for r in dims["week"]["rows"]}
    assert week["up"]["matched"]["edge"] == 1.0 and week["down"]["matched"]["edge"] == -0.5
    assert {r["name"] for r in dims["type_regime"]["rows"]} == {"一买 · 上涨", "三买 · 下跌"}


def test_regime_map():
    d0 = date(2024, 1, 1)
    up = [Bar(d0 + timedelta(days=i), 0, 0, 0, 100 + i) for i in range(120)]
    down = [Bar(d0 + timedelta(days=120 + i), 0, 0, 0, 219 - 2 * i) for i in range(100)]
    m = regime_map(up + down)
    assert m[up[100].dt] == "up" and m[down[-1].dt] == "down" and up[10].dt not in m


def test_target_hit_recorded_even_if_final_exit_is_stop():
    from app.services.backtest import _ControlSpec, _stat, backtest_series
    from app.services.strategy_config import DEFAULT_PARAMS

    d0 = date(2024, 1, 1)
    closes = [10.0] * 6 + [10.2, 10.5, 10.8, 10.6, 10.3, 10.0, 9.7, 9.4, 9.2, 9.0]
    bars = [Bar(d0 + timedelta(days=i), c, c * 1.01, c * 0.99, c, 1e5) for i, c in enumerate(closes)]
    params = {**DEFAULT_PARAMS, "slippage": 0.0}
    trades = backtest_series("sz000001", bars, params, warmup=5, control=_ControlSpec(prob=1.0, stop_pct=0.05, rr=1.0))
    first = trades[0]
    assert first["tags"]["target_hit"] and first["exit_reason"] != "目标一"
    assert _stat(trades)["target_hit_ratio"] > 0
