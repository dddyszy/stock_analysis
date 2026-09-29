from datetime import date

from app.db.models import StructureStateDaily
from app.services.structure_events import detect

ZS = [{"name": "中枢上沿 ZG", "price": 11.0, "kind": "zg"}, {"name": "中枢下沿 ZD", "price": 10.0, "kind": "zd"}]
SIG = {"type": "B3", "name": "三买", "date": "2026-09-20", "confirmed": False, "price": 11.2}


def _st(state, price, upper, lower, signal=None, levels=None, d=date(2026, 9, 28)):
    return StructureStateDaily(code="sh600000", trade_date=d, state=state, price=price, upper=upper, lower=lower,
                               levels=levels if levels is not None else ZS, signal=signal)


def _types(evs):
    return {e["type"] for e in evs}


def test_no_previous_state_no_events():
    assert detect(None, _st("inside", 10.5, 11.0, 10.0)) == []


def test_state_change():
    evs = detect(_st("inside", 10.5, 11.0, 10.0, d=date(2026, 9, 25)), _st("above_up", 11.3, 11.5, 11.0))
    assert _types(evs) == {"state_change", "break_upper"}


def test_buy_signal_invalidated_is_important():
    prev = _st("B3_pending", 11.3, 12.5, 10.9, signal=SIG, levels=[{"name": "认错位", "price": 10.9, "kind": "stop"}, *ZS], d=date(2026, 9, 25))
    cur = _st("inside", 10.6, 11.0, 10.0)
    evs = detect(prev, cur)
    assert "signal_invalid" in _types(evs)
    assert next(e for e in evs if e["type"] == "signal_invalid")["level"] == "important"
    assert "state_change" not in _types(evs)


def test_signal_confirmed_and_new_zhongshu():
    prev = _st("B3_pending", 11.3, 12.5, 10.9, signal=SIG, d=date(2026, 9, 25))
    cur = _st("B3_confirmed", 11.5, 12.5, 10.9, signal={**SIG, "confirmed": True},
              levels=[{"name": "中枢上沿 ZG", "price": 11.4, "kind": "zg"}, {"name": "中枢下沿 ZD", "price": 10.6, "kind": "zd"}])
    assert _types(detect(prev, cur)) == {"signal_confirmed", "new_zhongshu", "state_change"}


def test_break_lower_names_the_level():
    prev = _st("inside", 10.5, 11.0, 10.0, d=date(2026, 9, 25))
    (e, *_) = [x for x in detect(prev, _st("below_down", 9.7, 10.0, 9.7)) if x["type"] == "break_lower"]
    assert "中枢下沿" in e["title"] and e["level"] == "important"
