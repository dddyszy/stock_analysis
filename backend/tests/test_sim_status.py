import pytest

from app.services.sim_trade import _norm_status


@pytest.mark.parametrize("raw,expected", [
    ("未成交", "pending"), ("待成交", "pending"), ("已报", "pending"), ("success", "pending"), (None, "pending"),
    ("部分成交", "partial"), ("部成", "partial"),
    ("已成交", "filled"), ("全部成交", "filled"), ("成交", "filled"), ("filled", "filled"),
    ("已撤", "cancelled"), ("部成已撤", "cancelled"), ("废单", "rejected"),
])
def test_norm_status(raw, expected):
    assert _norm_status(raw) == expected


def test_norm_status_uses_filled_quantity():
    assert _norm_status("未成交", 100, 200) == "partial"
    assert _norm_status(None, 200, 200) == "filled"
    assert _norm_status(None, 100, 200) == "partial"
