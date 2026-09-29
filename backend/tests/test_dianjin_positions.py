from datetime import date, timedelta

from app.analysis.value import Event, FinRow, build_series
from app.db.models import PositionPlan
from app.services import dianjin_positions


def _days(n: int) -> list[date]:
    out, d = [], date(2024, 1, 2)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _series(last_px: float, eps: float = 1.0):
    ds = _days(140)
    bars = [(d, 10.0, 10.0) for d in ds[:-1]] + [(ds[-1], last_px, last_px)]
    fin = [FinRow(date(2020 + k, 12, 31), date(2021 + k, 3, 20), roe_weighted=20, eps_ttm=eps, np_ttm=eps * 1e9) for k in range(3)]
    return build_series("sh600000", bars, [Event(ds[100], cash=0.5)], fin)


def _plan() -> PositionPlan:
    return PositionPlan(id=999999, code="sh600000", strategy="dianjin_v1", remaining_qty=500, entry_price=9.0, current_stop=0.0)


def test_close_when_above_sell_line(monkeypatch):
    monkeypatch.setattr(dianjin_positions, "load_series", lambda code: _series(12.0))
    adv, _, close = dianjin_positions.evaluate_position(_plan())
    assert adv.action == "close" and adv.quantity == 500 and close == 12.0


def test_hold_below_sell_line_even_if_screen_fails(monkeypatch):
    monkeypatch.setattr(dianjin_positions, "load_series", lambda code: _series(9.5, eps=-0.5))
    adv, _, _ = dianjin_positions.evaluate_position(_plan())
    assert adv.action == "hold"
    assert any("距卖出线" in r for r in adv.reasons)
    assert any("不再满足" in r for r in adv.reasons)
