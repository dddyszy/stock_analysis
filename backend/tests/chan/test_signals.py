from app.chan import analyze
from tests.chan.helpers import zigzag


def _downtrend_with_divergence():
    lead = [100 + i * 0.7 for i in range(40)]  # 先上涨让 MACD 充分初始化
    pivots = [128, 100, 108, 102, 107, 97, 104, 99, 112, 106]
    legs = [5, 6, 6, 6, 16, 6, 6, 8, 6]
    return zigzag(pivots, legs, lead=lead)


def test_first_and_second_buy_on_divergence():
    r = analyze(_downtrend_with_divergence())
    types = [s.type for s in r.signals]
    b1 = [s for s in r.signals if s.type == "B1"]
    assert b1, types
    assert b1[-1].price == 96.8
    assert b1[-1].extra["divergence"]["leave_area"] < b1[-1].extra["divergence"]["enter_area"]
    b2 = [s for s in r.signals if s.type == "B2"]
    assert b2, types
    assert b2[-1].price == 98.8
    assert b2[-1].stop_price == b1[-1].price


def test_no_first_buy_without_divergence():
    lead = [100 + i * 0.7 for i in range(40)]
    # 离开段比进入段更陡更长，没有背驰
    pivots = [128, 118, 124, 119, 123, 90, 96, 93]
    legs = [5, 6, 6, 6, 5, 6, 6]
    r = analyze(zigzag(pivots, legs, lead=lead))
    assert not [s for s in r.signals if s.type == "B1"]


def test_invalidated_signal_when_stop_broken():
    bars = _downtrend_with_divergence()
    # 在末尾追加一段跌破一买低点的走势
    from tests.chan.helpers import B

    last = bars[-1]
    for i in range(1, 12):
        p = last.close - i * 1.2
        bars.append(B(last.dt.replace(day=1) if False else last.dt.fromordinal(last.dt.toordinal() + i), p + 1.2, p + 1.4, p - 0.2, p))
    r = analyze(bars)
    b1 = [s for s in r.signals if s.type == "B1" and s.price == 96.8]
    assert b1 and b1[0].extra.get("invalidated")


def test_to_dict_is_json_ready():
    import json

    r = analyze(_downtrend_with_divergence())
    payload = r.to_dict()
    json.dumps(payload)
    assert payload["bars"] and payload["bis"] and "summary" in payload
