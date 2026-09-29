from fastapi import APIRouter

from app.services import structure_events

router = APIRouter(prefix="/api/structure-events", tags=["structure-events"])


@router.get("")
def events(days: int = 30, code: str | None = None, level: str | None = None) -> dict:
    return structure_events.list_events(days, code, level)
