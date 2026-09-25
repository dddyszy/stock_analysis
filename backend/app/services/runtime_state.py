"""运行时状态的键值读写（runtime_state 表）。"""

from datetime import datetime

from app.db.models import RuntimeState
from app.db.session import session_scope


def get_state(key: str, default=None):
    with session_scope() as db:
        row = db.get(RuntimeState, key)
        return row.value if row is not None else default


def set_state(key: str, value) -> None:
    with session_scope() as db:
        row = db.get(RuntimeState, key)
        if row is None:
            db.add(RuntimeState(key=key, value=value))
        else:
            row.value = value


def cooldown_until(tool: str) -> datetime | None:
    """某个 MCP 工具的配额冷却截止时间；已过期返回 None。"""
    raw = (get_state(f"quota:{tool}") or {}).get("until")
    if not raw:
        return None
    until = datetime.fromisoformat(raw)
    return until if until > datetime.now() else None


def set_cooldown(tool: str, until: datetime, reason: str) -> None:
    set_state(f"quota:{tool}", {"until": until.isoformat(timespec="seconds"), "reason": reason})
