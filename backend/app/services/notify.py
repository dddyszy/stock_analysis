"""系统内通知。同一事件同一天只记一次；以后接企业微信、邮件时往 CHANNELS 里加发送函数即可。"""

import logging
from collections.abc import Callable
from datetime import date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert

from app.db.models import NotifyLog
from app.db.session import session_scope

logger = logging.getLogger(__name__)

LEVELS = ("info", "warning", "error", "important")
CHANNELS: list[Callable[[dict], None]] = []


def notify(event: str, title: str, message: str = "", level: str = "warning", key: str = "", day: date | None = None) -> bool:
    """记录一条通知，返回是否为新通知（当天已有同一事件则忽略）。"""
    assert level in LEVELS
    dedupe = f"{event}:{(day or date.today()):%Y%m%d}:{key}"[:191]
    payload = {"event": event, "level": level, "title": title[:255], "message": (message or "")[:2000], "dedupe_key": dedupe}
    try:
        with session_scope() as db:
            created = db.execute(insert(NotifyLog).prefix_with("IGNORE").values(**payload)).rowcount > 0
    except Exception:  # noqa: BLE001
        logger.exception("记录通知失败：%s", title)
        return False
    if created:
        logger.info("通知[%s] %s %s", level, title, message)
        for send in CHANNELS:
            try:
                send(payload)
            except Exception:  # noqa: BLE001
                logger.exception("通知渠道发送失败")
    return created


def _to_dict(n: NotifyLog) -> dict:
    return {"id": n.id, "event": n.event, "level": n.level, "title": n.title, "message": n.message,
            "is_read": n.is_read, "created_at": n.created_at.isoformat() if isinstance(n.created_at, datetime) else None}


def recent(limit: int = 50) -> dict:
    with session_scope() as db:
        rows = db.execute(select(NotifyLog).order_by(NotifyLog.id.desc()).limit(limit)).scalars().all()
        unread = db.scalar(select(func.count()).select_from(NotifyLog).where(NotifyLog.is_read.is_(False))) or 0
        return {"items": [_to_dict(n) for n in rows], "unread": unread}


def unread_count() -> int:
    with session_scope() as db:
        return db.scalar(select(func.count()).select_from(NotifyLog).where(NotifyLog.is_read.is_(False))) or 0


def mark_all_read() -> int:
    with session_scope() as db:
        return db.execute(update(NotifyLog).where(NotifyLog.is_read.is_(False)).values(is_read=True)).rowcount
