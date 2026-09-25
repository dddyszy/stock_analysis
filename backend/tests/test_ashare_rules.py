from dataclasses import dataclass
from datetime import date

from app.services.ashare_rules import (
    board,
    clamp_to_limits,
    disclosure_deadline,
    is_limit_down,
    is_limit_up,
    is_one_price_limit_down,
    is_one_price_limit_up,
    limit_pct,
    limit_prices,
    volume_lot,
)


@dataclass
class B:
    open: float
    high: float
    low: float
    close: float


def test_board_and_limit_pct():
    assert board("sh600519") == "main" and limit_pct("sh600519") == 0.10
    assert limit_pct("sh600519", is_st=True) == 0.05
    assert board("sz300750") == "gem" and limit_pct("sz300750", is_st=True) == 0.20
    assert board("sh688981") == "star" and limit_pct("sh688981") == 0.20
    assert board("bj830799") == "bj" and limit_pct("bj830799") == 0.30
    assert limit_pct("sz000001") == 0.10


def test_volume_lot():
    assert volume_lot("sh688120") == 1
    assert volume_lot("sh600519") == 100 and volume_lot("sz300750") == 100


def test_limit_prices_round_half_up():
    assert limit_prices(10.0, "sh600000") == (11.0, 9.0)
    assert limit_prices(12.35, "sh600000") == (13.59, 11.12)
    assert limit_prices(10.0, "sz300001") == (12.0, 8.0)


def test_limit_detection_and_one_price():
    assert is_limit_up(10.0, 11.0, "sh600000")
    assert not is_limit_up(10.0, 10.95, "sh600000")
    assert is_limit_down(10.0, 8.0, "sz300001")
    assert not is_limit_down(10.0, 9.0, "sz300001")
    assert is_one_price_limit_up(B(11, 11, 11, 11), 10.0, "sh600000")
    assert not is_one_price_limit_up(B(10.5, 11, 10.4, 11), 10.0, "sh600000")
    assert is_one_price_limit_down(B(9.5, 9.5, 9.5, 9.5), 10.0, "sh600000", is_st=True)


def test_clamp_to_limits():
    assert clamp_to_limits(12.5, 10.0, "sh600000") == 11.0
    assert clamp_to_limits(12.5, 10.0, "sz300001") == 12.0
    assert clamp_to_limits(7.0, 10.0, "sh600000") == 9.0


def test_disclosure_deadline():
    assert disclosure_deadline(date(2025, 3, 31)) == date(2025, 4, 30)
    assert disclosure_deadline(date(2025, 6, 30)) == date(2025, 8, 31)
    assert disclosure_deadline(date(2025, 9, 30)) == date(2025, 10, 31)
    assert disclosure_deadline(date(2025, 12, 31)) == date(2026, 4, 30)


def test_plan_pool_changes():
    from app.services.sync import plan_pool_changes

    prev = {"sh600001": 0, "sh600002": 1, "sh600003": 1, "sh600004": 0}
    seen = {"sh600001", "sh600009"}
    # 部分行业失败：不动任何股票
    assert plan_pool_changes(prev, seen, complete=False, trading=set()) == ({}, [])
    counts, delisted = plan_pool_changes(prev, seen, complete=True, trading={"sh600003"})
    # sh600002 第二次缺席且查不到行情 → 退市；sh600003 仍有行情 → 只累计；sh600004 第一次缺席
    assert delisted == ["sh600002"]
    assert counts == {"sh600003": 2, "sh600004": 1}


def test_plan_group_removal():
    from app.services.app_sync import plan_group_removal

    removed, moved, kept = plan_group_removal(
        ["sh600001", "sh600002", "sh600003"], user_owned={"sh600002"}, custom={"10": {"sh600003"}},
    )
    assert removed == ["sh600001"] and moved == {"sh600003": "10"} and kept == ["sh600002"]
