from datetime import date

from app.chan import analyze
from app.chan.divergence import MoveStats, compare
from app.chan.types import DOWN, UP
from app.providers.mock import UNIVERSE, _series


def test_multi_indicator_strong_divergence():
    enter = MoveStats(area=10.0, dif=-2.0, amplitude=20.0, bars=10, avg_volume=100.0)
    leave = MoveStats(area=4.0, dif=-1.0, amplitude=8.0, bars=10, avg_volume=60.0)
    r = compare(enter, leave, DOWN, ratio=0.9)
    assert r["divergent"] and r["dif_divergent"] and r["slope_divergent"] and r["volume_divergent"]
    assert r["agree"] == 4 and r["strong"]
    assert 0 < r["strength"] <= 1


def test_area_is_required_and_weak_divergence():
    enter = MoveStats(area=10.0, dif=2.0, amplitude=20.0, bars=10, avg_volume=100.0)
    no_area = MoveStats(area=12.0, dif=1.0, amplitude=8.0, bars=10, avg_volume=50.0)
    assert not compare(enter, no_area, UP, ratio=0.9)["divergent"]
    weak = MoveStats(area=8.0, dif=3.0, amplitude=30.0, bars=10, avg_volume=120.0)
    r = compare(enter, weak, UP, ratio=0.9)
    assert r["divergent"] and r["agree"] == 1 and not r["strong"]


def test_segment_level_signals_exist_and_reference_segments():
    seg_signals = []
    for s in UNIVERSE[:60]:
        bars = list(_series(s.code, date(2026, 9, 1)))[-900:]
        r = analyze(bars, "day")
        for sig in r.signals:
            assert sig.scope in ("bi", "seg")
            if sig.scope == "seg":
                assert 0 <= sig.bi_idx < len(r.segments)
                if sig.is_buy:
                    assert sig.stop_price is not None and sig.target1 and sig.target1 > sig.price
                seg_signals.append(sig)
    assert seg_signals, "合成行情中应能找到线段级别的买卖点"


def test_first_buy_carries_leave_range():
    for s in UNIVERSE[:60]:
        r = analyze(list(_series(s.code, date(2026, 9, 1)))[-900:], "day")
        b1 = [x for x in r.signals if x.type == "B1"]
        if b1:
            lr = b1[0].extra["leave_range"]
            assert lr[0] <= lr[1]
            assert "agree" in b1[0].extra["divergence"]
            return
    raise AssertionError("合成行情中应能找到一买")
