from datetime import date, timedelta

import pytest

from app.analysis.adjust import to_ratio_adjusted, to_raw, unadjust_maps
from app.analysis.value import Event
from app.providers.base import Bar
from app.services.price_adjust import fit_coefficients, max_abnormal_jump


def _bar(d: date, px: float) -> Bar:
    return Bar(dt=d, open=px, high=px, low=px, close=px, volume=1.0)


def test_tencent_qfq_back_to_raw_with_transfer_and_cash():
    # sh600039：2023-06-15「10派9.1元转4股」，之后的现金分红合计 1.423；除权前一天原价 14.60，腾讯前复权 8.356
    ex = date(2023, 6, 15)
    later = Event(date(2024, 6, 20), cash=1.423)
    bars = [_bar(date(2023, 6, 14), 8.356), _bar(ex, 8.267), _bar(date(2024, 6, 21), 9.0)]
    raw = to_raw(bars, [Event(ex, cash=0.91, transfer=0.4), later])
    assert raw[0].close == pytest.approx(14.60, abs=0.002)
    assert raw[1].close == pytest.approx(8.267 + 1.423)
    assert raw[2].close == 9.0


def test_ratio_adjusted_keeps_returns_and_latest_price():
    d0 = date(2024, 1, 2)
    raw_px = [10.0, 10.0, 9.5, 9.5]  # 第三天除息 0.5
    ev = [Event(d0 + timedelta(days=2), cash=0.5)]
    qfq = [_bar(d0 + timedelta(days=i), p - (0.5 if i < 2 else 0)) for i, p in enumerate(raw_px)]
    out = to_ratio_adjusted(qfq, ev)
    assert out[-1].close == 9.5
    assert out[2].close / out[1].close == pytest.approx(1.0)  # 除息不算涨跌
    assert out[0].close == pytest.approx(9.5)


def test_unadjust_maps_compose_from_latest():
    ds = [date(2024, 1, 1), date(2024, 6, 1), date(2025, 1, 1)]
    maps = unadjust_maps(ds, [Event(date(2024, 3, 1), cash=0.2, transfer=1.0), Event(date(2024, 9, 1), cash=0.1)])
    assert maps[2] == (1.0, 0.0)
    assert maps[1] == pytest.approx((1.0, 0.1))
    assert maps[0] == pytest.approx((2.0, 0.4))


def test_fit_rights_issue_coefficients():
    # 10 配 3、配股价 5 元：除权参考价 = (P + 0.3×5) / 1.3，腾讯前复权 q = (P − c) / s，s = 1.3，c = −1.5
    d0 = date(2024, 3, 1)
    raw = {d0 + timedelta(days=i): 10.0 + 0.1 * i for i in range(6)}
    qfq = [_bar(d, (p + 1.5) / 1.3) for d, p in raw.items()]
    s, c = fit_coefficients(qfq, raw, later=[])
    assert s == pytest.approx(1.3) and c == pytest.approx(-1.5)


def test_fit_rejects_inconsistent_points():
    d0 = date(2024, 3, 1)
    raw = {d0 + timedelta(days=i): 10.0 + (i % 2) for i in range(6)}
    qfq = [_bar(d, 5.0 + 0.1 * i) for i, d in enumerate(raw)]
    assert fit_coefficients(qfq, raw, later=[]) is None


def test_abnormal_jump_count():
    d0 = date(2024, 1, 2)
    bars = [_bar(d0, 10), _bar(d0 + timedelta(days=1), 10.5), _bar(d0 + timedelta(days=2), 6.0)]
    assert max_abnormal_jump(bars, 0.10) == 1
