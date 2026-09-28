from datetime import date, timedelta

from app.analysis.value import Event, FinRow, build_series
from app.strategies.dianjin import VARIANTS, evaluate, zone_of


def _days(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _series(last_px: float, roe=(18.0, 17.0, 16.0), np_ttm=1e9, eps=1.0, cash=0.5):
    ds = _days(date(2024, 1, 2), 140)
    bars = [(d, 10.0, 10.0) for d in ds[:-1]] + [(ds[-1], last_px, last_px)]
    fin = [FinRow(date(2020 + k, 12, 31), date(2021 + k, 3, 20), roe_weighted=r, eps_ttm=eps, np_ttm=np_ttm)
           for k, r in enumerate(reversed(roe))]
    return build_series("sh600000", bars, [Event(ds[100], cash=cash)], fin)


def test_zone_thresholds():
    p = VARIANTS["dianjin_v2"]
    assert zone_of(0.87, p) == "buy" and zone_of(0.90, p) == "near_buy"
    assert zone_of(1.0, p) == "middle" and zone_of(1.13, p) == "sell" and zone_of(None, p) == "insufficient"


def test_v2_passes_and_detects_buy_zone():
    ev = evaluate(_series(8.0), VARIANTS["dianjin_v2"])
    assert ev.passed, ev.summary
    assert ev.zone == "buy"
    assert {c.key for c in ev.criteria} == {"pe", "dy", "mv", "roe"}


def test_roe_threshold_depends_on_market_cap():
    small_low_roe = evaluate(_series(8.5, roe=(12.0, 12.0, 12.0)), VARIANTS["dianjin_v2"])
    assert not small_low_roe.passed and "近三年 ROE" in small_low_roe.summary
    big_low_roe = evaluate(_series(8.5, roe=(12.0, 12.0, 12.0), np_ttm=1e10), VARIANTS["dianjin_v2"])
    assert big_low_roe.passed


def test_original_version_ignores_roe_and_uses_3pct_dividend():
    s = _series(9.0, roe=(5.0, 5.0, 5.0), cash=0.32)
    assert evaluate(s, VARIANTS["dianjin_v1"]).passed
    assert not evaluate(s, VARIANTS["dianjin_v2"]).passed


def test_loss_making_fails_pe():
    ev = evaluate(_series(8.5, eps=-0.2, np_ttm=-2e8), VARIANTS["dianjin_v1"])
    pe = next(c for c in ev.criteria if c.key == "pe")
    assert pe.passed is False and pe.value == "亏损"
