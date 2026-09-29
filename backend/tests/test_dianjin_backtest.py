from datetime import date, timedelta

import numpy as np
import pytest

from app.research.dianjin_backtest import SimParams, StockData, Universe, metrics, random_control, simulate


def _stock(code: str, prices: list[float], ratios: list[float], one_up=(), one_down=()) -> StockData:
    ds = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(prices))]
    n = len(ds)
    up = np.zeros(n, dtype=bool)
    dn = np.zeros(n, dtype=bool)
    up[list(one_up)] = True
    dn[list(one_down)] = True
    px = np.array(prices, dtype=float)
    return StockData(code, ds, {d: i for i, d in enumerate(ds)}, px, px, np.array(ratios, dtype=float), up, dn,
                     [10.0] * n, [5.0] * n, [1e10] * n, [(20.0, 20.0, 20.0)] * n)


def _universe(panel: dict[str, StockData]) -> Universe:
    uni = Universe()
    for code, st in panel.items():
        uni.passed[code] = np.ones(len(st.dates), dtype=bool)
        for i, d in enumerate(st.dates):
            uni.by_day.setdefault(d, []).append((float(st.ratio[i]), code))
    for d in uni.by_day:
        uni.by_day[d].sort()
    return uni


NO_COST = SimParams(slippage=0.0, commission=0.0, cash_rate=0.0, max_positions=1)


def test_buys_next_open_and_sells_after_crossing_sell_line():
    st = _stock("sh600001", [10, 10, 10, 11, 12, 13, 13], [1.0, 0.85, 0.9, 1.0, 1.1, 1.15, 1.15])
    panel = {st.code: st}
    res = simulate(panel, _universe(panel), st.dates, NO_COST)
    (t,) = res["trades"]
    assert t["entry_date"] == st.dates[2] and t["exit_date"] == st.dates[6]
    assert t["ret"] == pytest.approx(13 / 10 - 1 - 0.001, abs=2e-3)  # 卖出印花税
    assert res["nav"][-1] == pytest.approx(1 + t["ret"])


def test_one_price_limit_up_blocks_buy_and_limit_down_delays_sell():
    st = _stock("sh600001", [10, 10, 11, 11, 12, 13, 12, 12], [1.0, 0.85, 0.85, 0.9, 1.0, 1.15, 1.1, 1.1], one_up=[2], one_down=[6])
    panel = {st.code: st}
    (t,) = simulate(panel, _universe(panel), st.dates, NO_COST)["trades"]
    assert t["entry_date"] == st.dates[3]
    assert t["exit_date"] == st.dates[7]


def test_max_positions_and_deepest_discount_first():
    a = _stock("sh600001", [10] * 5, [1.0, 0.86, 0.9, 0.9, 0.9])
    b = _stock("sh600002", [10] * 5, [1.0, 0.80, 0.9, 0.9, 0.9])
    panel = {a.code: a, b.code: b}
    res = simulate(panel, _universe(panel), a.dates, NO_COST)
    assert [o["code"] for o in res["open"]] == ["sh600002"]


def test_stop_loss_exit():
    st = _stock("sh600001", [10, 10, 10, 7.5, 7.5, 7.5], [1.0, 0.85, 0.9, 0.7, 0.7, 0.7])
    panel = {st.code: st}
    (t,) = simulate(panel, _universe(panel), st.dates, SimParams(slippage=0, commission=0, cash_rate=0, max_positions=1, stop_loss=0.2))["trades"]
    assert t["reason"].startswith("跌破买入价")


def test_metrics_and_random_control_run():
    st = _stock("sh600001", [10, 10, 10, 11, 12, 13, 13], [1.0, 0.85, 0.9, 1.0, 1.1, 1.15, 1.15])
    panel = {st.code: st}
    uni = _universe(panel)
    res = simulate(panel, uni, st.dates, NO_COST)
    m = metrics(st.dates, res)
    assert m["trades"] == 1 and m["win_rate"] == 100.0
    rc = random_control(panel, uni, st.dates, res["trades"], NO_COST, draws=10)
    assert rc["draws"] == 10 and rc["real_avg"] == pytest.approx(rc["random_median"])
