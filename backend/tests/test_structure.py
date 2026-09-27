from datetime import date

from app.analysis.structure import STATE_NAMES, StructureState, classify, first_touch, outcome, scenarios
from app.chan import analyze
from app.providers.mock import UNIVERSE, _series


def test_classify_invariants_on_mock_universe():
    seen = set()
    for s in UNIVERSE[:60]:
        bars = list(_series(s.code, date(2026, 9, 1)))[-500:]
        for cut in (300, 400, 500):
            st = classify(analyze(bars[:cut], "day"))
            assert st.key in STATE_NAMES
            seen.add(st.key)
            if st.key == "insufficient":
                continue
            assert st.lower is not None and st.upper is not None
            assert st.lower <= st.price <= st.upper, (st.key, st.lower, st.price, st.upper)
            assert st.continuation in ("up", "down", "both")
            assert len(scenarios(st)) == 2
    assert len(seen) >= 5, seen


def test_first_touch_and_outcome():
    st = StructureState("B3_pending", upper=11.0, lower=9.5, continuation="up", price=10.0)
    assert first_touch(st, [10.2, 10.8, 11.1, 9.0]) == "up"
    assert outcome(st, "up") == "continue"
    assert first_touch(st, [10.2, 9.4, 11.5]) == "down"
    assert outcome(st, "down") == "exception"
    assert outcome(st, first_touch(st, [10.0, 10.5])) == "undecided"
    down = StructureState("below_down", upper=12.0, lower=9.0, continuation="down", price=10.0)
    assert outcome(down, "down") == "continue" and outcome(down, "up") == "exception"
    box = StructureState("inside", upper=11.0, lower=9.0, continuation="both", price=10.0)
    assert outcome(box, "up") == "break_up" and outcome(box, "down") == "break_down"
