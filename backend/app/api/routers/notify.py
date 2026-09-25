from fastapi import APIRouter

from app.services import notify as notify_service

router = APIRouter(prefix="/api/notify", tags=["notify"])


@router.get("")
def recent(limit: int = 50) -> dict:
    return notify_service.recent(limit)


@router.get("/unread")
def unread() -> dict:
    return {"unread": notify_service.unread_count()}


@router.post("/read-all")
def read_all() -> dict:
    return {"updated": notify_service.mark_all_read()}
