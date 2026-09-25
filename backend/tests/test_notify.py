import asyncio
from datetime import date, datetime

import pytest
from sqlalchemy import delete


@pytest.fixture
def clean_notify(mock_db):
    from app.db.models import NotifyLog
    from app.db.session import session_scope

    def _clean():
        with session_scope() as db:
            db.execute(delete(NotifyLog).where(NotifyLog.event.like("test_%") | NotifyLog.dedupe_key.like("pipeline_missing:%:2000-%")))

    _clean()
    yield
    _clean()


def test_notify_dedupes_same_event_same_day(clean_notify):
    from app.services.notify import mark_all_read, notify, recent

    assert notify("test_event", "测试通知", "第一次", "warning", key="a")
    assert not notify("test_event", "测试通知", "同一天同一事件", "warning", key="a")
    assert notify("test_event", "测试通知", "不同对象", "warning", key="b")
    items = [n for n in recent(200)["items"] if n["event"] == "test_event"]
    assert len(items) == 2 and not items[0]["is_read"]
    mark_all_read()
    assert all(n["is_read"] for n in recent(200)["items"] if n["event"] == "test_event")


def test_missed_pipeline_on_startup(clean_notify, monkeypatch):
    from app.jobs import watchdog

    async def fake_trading(d: date) -> bool:
        return d.weekday() < 5

    monkeypatch.setattr(watchdog, "is_trading", fake_trading)
    # 2000-01-08 是周六，上一个交易日是 01-07（周五），那天没有流水线记录
    missed = asyncio.run(watchdog.check_missed_on_startup(datetime(2000, 1, 8, 10, 0)))
    assert missed == date(2000, 1, 7)
    # 17:30 之前启动，只检查前一个交易日
    assert asyncio.run(watchdog.check_missed_on_startup(datetime(2000, 1, 5, 9, 0))) == date(2000, 1, 4)


def test_finance_cooldown_skips_fetch(mock_db, monkeypatch):
    from datetime import timedelta

    from app.services import sync
    from app.services.runtime_state import cooldown_until, set_cooldown, set_state

    set_cooldown("data_finance", datetime.now() + timedelta(minutes=5), "测试")
    try:
        assert cooldown_until("data_finance") is not None
        monkeypatch.setattr(sync, "_fresh_fundamental_codes", lambda codes, days: set())

        def boom(*_a, **_k):
            raise AssertionError("冷却期内不应调用接口")

        monkeypatch.setattr(sync, "create_provider", boom)
        stats = asyncio.run(sync.sync_finance_details(["sz000001", "sz000002"], time_budget=60))
        assert stats["skipped"] == 2 and stats["cooldown_until"]
    finally:
        set_state("quota:data_finance", None)
    assert cooldown_until("data_finance") is None
