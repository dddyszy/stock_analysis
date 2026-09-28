from datetime import date, timedelta

import pytest

from app.analysis.value import Event, FinRow, build_series, max_dividend_yield, parse_fh
from app.services.value_data import event_rows, finance_rows, passes_basic


def _days(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_parse_fh():
    assert parse_fh("10派19.72元") == pytest.approx((1.972, 0, 0))
    assert parse_fh("10送3转4派2元(含税)") == pytest.approx((0.2, 0.3, 0.4))
    assert parse_fh("10转增4股派1元") == pytest.approx((0.1, 0, 0.4))
    assert parse_fh(None) == (0.0, 0.0, 0.0)
    assert parse_fh("") == (0.0, 0.0, 0.0)


def test_cash_dividend_adjustment_and_yield():
    ds = _days(date(2024, 1, 1), 5)
    bars = [(d, 10.0, 10.0) for d in ds[:2]] + [(d, 9.5, 9.5) for d in ds[2:]]
    s = build_series("sh600000", bars, [Event(ds[2], cash=0.5)], [])
    assert s.factor[-1] == 1.0
    assert s.adj_close(1) == pytest.approx(9.5)
    assert s.adj_close(2) == pytest.approx(9.5)
    assert s.div_ps[1] == 0 and s.div_ps[2] == pytest.approx(0.5)
    assert s.dy[2] == pytest.approx(0.5 / 9.5 * 100)


def test_bonus_shares_adjustment_and_per_share_dividend():
    ds = _days(date(2024, 1, 1), 6)
    bars = [(ds[0], 20.0, 20.0), (ds[1], 20.0, 20.0), (ds[2], 19.0, 19.0), (ds[3], 9.5, 9.5), (ds[4], 9.5, 9.5), (ds[5], 9.5, 9.5)]
    events = [Event(ds[2], cash=1.0), Event(ds[3], transfer=1.0)]
    s = build_series("sh600000", bars, events, [])
    assert s.adj_close(0) == pytest.approx(9.5)
    assert s.adj_close(2) == pytest.approx(9.5)
    assert s.div_ps[2] == pytest.approx(1.0)
    assert s.div_ps[3] == pytest.approx(0.5)


def test_dividend_drops_out_after_a_year():
    ds = [date(2024, 7, 1), date(2024, 7, 2), date(2025, 7, 1), date(2025, 7, 2)]
    bars = [(d, 10.0, 10.0) for d in ds]
    s = build_series("sh600000", bars, [Event(ds[1], cash=0.4)], [])
    assert s.div_ps[2] == pytest.approx(0.4)
    assert s.div_ps[3] == 0
    assert max_dividend_yield(bars, [Event(ds[1], cash=0.4)]) == pytest.approx(4.0)


def test_finance_only_visible_after_publication():
    ds = _days(date(2024, 3, 1), 60)
    bars = [(d, 10.0, 10.0) for d in ds]
    fin = [
        FinRow(date(2021, 12, 31), date(2022, 3, 20), roe_weighted=16, eps_ttm=0.9, np_ttm=0.9e9),
        FinRow(date(2022, 12, 31), date(2023, 3, 20), roe_weighted=17, eps_ttm=0.95, np_ttm=0.95e9),
        FinRow(date(2023, 12, 31), date(2024, 3, 25), roe_weighted=18, eps_ttm=1.0, np_ttm=1.0e9),
    ]
    s = build_series("sh600000", bars, [], fin)
    before = s.index_on(date(2024, 3, 22))
    after = s.index_on(date(2024, 3, 26))
    assert s.roe3[before] is None
    assert s.pe[before] == pytest.approx(10 / 0.95)
    assert s.roe3[after] == (18, 17, 16)
    assert s.pe[after] == pytest.approx(10.0)
    assert s.mv[after] == pytest.approx(1e10)


def test_roe_needs_three_consecutive_years():
    ds = _days(date(2024, 6, 1), 5)
    fin = [
        FinRow(date(2020, 12, 31), date(2021, 3, 1), roe=20, eps_ttm=1, np_ttm=1e9),
        FinRow(date(2022, 12, 31), date(2023, 3, 1), roe=20, eps_ttm=1, np_ttm=1e9),
        FinRow(date(2023, 12, 31), date(2024, 3, 1), roe=20, eps_ttm=1, np_ttm=1e9),
    ]
    s = build_series("sh600000", [(d, 10.0, 10.0) for d in ds], [], fin)
    assert s.roe3[-1] is None


def test_ma120_uses_adjusted_close():
    ds = _days(date(2023, 1, 2), 130)
    bars = [(d, 10.0, 10.0) for d in ds]
    s = build_series("sh600000", bars, [], [])
    assert s.ma120[118] is None
    assert s.ma120[119] == pytest.approx(10.0)
    assert s.ma120[-1] == pytest.approx(10.0)


def test_event_and_finance_row_parsing():
    rows = event_rows("sh600036", [{"nd": "2023", "djr": "2024-07-10", "cqr": "2024-07-11", "FHcontent": "10派19.72元"},
                                   {"nd": "2009", "djr": "2010-03-04", "cqr": "2010-03-15"}])
    assert rows[0]["ex_date"] == date(2024, 7, 11) and rows[0]["cash"] == pytest.approx(1.972)
    assert rows[1]["cash"] == 0 and rows[1]["content"] is None
    fin = finance_rows("sh600036", [{"EndDate": "2024-12-31", "InfoPublDate": "2025-03-26 00:00:00 +0800 CST", "ROE": "12.1",
                                     "ROEWeighted": "14.49", "EPSTTM": "5.66", "NPParentCompanyOwnersTTM": "148391000000"}])
    assert fin[0]["publ_date"] == date(2025, 3, 26)
    assert fin[0]["roe_weighted"] == pytest.approx(14.49)


def test_passes_basic():
    ds = _days(date(2024, 6, 3), 3)
    fin = [FinRow(date(2023, 12, 31), date(2024, 3, 1), eps_ttm=1.0, np_ttm=1e9)]
    s = build_series("sh600000", [(d, 10.0, 10.0) for d in ds], [Event(ds[1], cash=0.5)], fin)
    assert not passes_basic(s, 0)
    assert passes_basic(s, 2)
    assert not passes_basic(s, 2, min_mv=2e10)
