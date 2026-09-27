import random
from dataclasses import replace
from datetime import date, timedelta

from app.providers.base import Bar
from app.research.analysis import bh_adjust, factor_research
from app.research.factors import EntryCtx, compute_factors


def _bars(n: int = 300) -> list[Bar]:
    rng = random.Random(3)
    d0, price, out = date(2024, 1, 1), 10.0, []
    for i in range(n):
        price *= 1 + rng.uniform(-0.03, 0.035)
        out.append(Bar(d0 + timedelta(days=i), price, price * 1.02, price * 0.98, price, 1e5 * (1 + rng.random())))
    return out


def _ctx(bars: list[Bar], t: int) -> EntryCtx:
    bench = {b.dt: 100 + i * 0.1 for i, b in enumerate(bars)}
    ind = {b.dt: 50 + i * 0.05 for i, b in enumerate(bars)}
    return EntryCtx("sh688001", bars, t, bench=bench, bench_dates=sorted(bench), ind_index=ind, ind_dates=sorted(ind))


def test_factors_do_not_look_ahead():
    bars = _bars()
    t = 260
    before = compute_factors(_ctx(bars, t), control=True)
    future_changed = bars[: t + 1] + [replace(b, close=b.close * 3, high=b.high * 3, volume=1e9) for b in bars[t + 1 :]]
    assert compute_factors(_ctx(future_changed, t), control=True) == before
    assert {"rs20", "rs60", "rs_ind20", "ind_rs20", "vol_5_60", "amount20", "atr_pct", "dist_high250"} <= set(before)


def test_factor_values():
    bars = _bars()
    t = 260
    f = compute_factors(_ctx(bars, t), control=True)
    stock20 = bars[t].close / bars[t - 20].close - 1
    bench20 = (100 + t * 0.1) / (100 + (t - 20) * 0.1) - 1
    assert abs(f["rs20"] - round(stock20 - bench20, 5)) < 1e-5
    assert f["dist_high250"] <= 0
    # 科创板成交量按股计，成交额 = 量 × 价
    amt = sum(b.volume * b.close for b in bars[t - 19 : t + 1]) / 20 / 1e8
    assert abs(f["amount20"] - round(amt, 5)) < 1e-5


def test_bh_adjust():
    q = bh_adjust([0.01, 0.04, 0.03, 0.2])
    assert [round(x, 3) for x in q] == [0.04, 0.053, 0.053, 0.2]


def _trade(sig: str, year: int, rs20: float, r: float, control: bool = False) -> dict:
    return {
        "signal_type": "RND" if control else sig, "scope": "rnd" if control else "bi", "entry_date": date(year, 6, 1),
        "r_multiple": r, "pnl_pct": r * 5, "excess": r * 3, "holding_days": 10, "is_control": control,
        "segment": "out" if year >= 2025 else "in", "tags": {"regime": "range", "f": {"rs20": rs20}},
    }


def test_factor_research_accepts_real_edge_and_rejects_noise():
    rng = random.Random(11)
    trades, control = [], []
    for year in (2022, 2023, 2024, 2025):
        for _ in range(300):
            v = rng.uniform(-0.2, 0.2)
            edge = 0.8 if v > 0.067 else 0.0  # 只有高档真正有效
            trades.append(_trade(rng.choice(["B1", "B2", "B3"]), year, v, edge + rng.gauss(0, 1)))
            control.append(_trade("", year, rng.uniform(-0.2, 0.2), rng.gauss(0, 1), control=True))
    out = factor_research(trades, control)
    assert "近 20 日相对强弱高档 · 全部买点" in out["accepted"]
    assert not any("低档" in name for name in out["accepted"])
    rs = next(f for f in out["factors"] if f["key"] == "rs20")
    high_all = next(r for r in rs["rows"] if r["group"] == "ALL" and r["bucket"] == "high")
    assert high_all["criteria"]["passed"] and high_all["rule"]["positive_years"] >= 3
    # 随机组里因子本身没有预测力
    assert not high_all["factor_alone"]["significant"]
