from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.mcp.client import mcp_client
from app.mcp.errors import McpError
from app.mcp.oauth import oauth_manager
from app.services.jobs import start_job

router = APIRouter(prefix="/api/auth/mcp", tags=["auth"])
settings = get_settings()


@router.get("/status")
def status() -> dict:
    return {**oauth_manager.status(), "provider": settings.data_provider, "mcp_url": settings.mcp_url,
            "redirect_uri": settings.mcp_oauth_redirect_uri}


@router.post("/start")
async def start() -> dict:
    return {"url": await oauth_manager.build_authorize_url()}


@router.get("/callback")
async def callback(code: str | None = None, state: str | None = None, error: str | None = None,
                   error_description: str | None = None) -> RedirectResponse:
    target = f"{settings.frontend_url}/settings"
    if error or not code or not state:
        msg = error_description or error or "缺少授权码"
        return RedirectResponse(f"{target}?auth=fail&msg={quote(msg)}")
    try:
        await oauth_manager.handle_callback(code, state)
    except McpError as exc:
        return RedirectResponse(f"{target}?auth=fail&msg={quote(str(exc))}")
    return RedirectResponse(f"{target}?auth=ok")


class ManualToken(BaseModel):
    access_token: str
    expires_in: int | None = None


@router.post("/token")
def manual_token(body: ManualToken) -> dict:
    oauth_manager.save_manual_token(body.access_token, body.expires_in)
    return oauth_manager.status()


@router.post("/logout")
def logout() -> dict:
    oauth_manager.revoke_local()
    return oauth_manager.status()


@router.post("/test")
async def test() -> dict:
    data = await mcp_client.call("data_quote", {"codes": "sh600519,sz000001"})
    return {"ok": True, "sample": data}


@router.post("/probe")
async def probe() -> dict:
    from app.mcp.probe import main as probe_main

    async def _run(ctx):
        ctx.update(message="探测各工具并保存响应样本", force=True)
        await probe_main()
        return {"ok": True}

    start_job("mcp_probe", _run)
    return {"started": True}
