"""westock-mcp 的 OAuth 2.1 授权：动态注册客户端、PKCE 授权码、refresh_token 续期。

SDK 自带的 OAuthClientProvider 在一次请求内阻塞等待浏览器回调，不适合 Web 后端，
所以这里把授权拆成 start / callback 两个 HTTP 步骤，token 持久化到 mcp_credential。
"""

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse

import httpx

from app.core.config import get_settings
from app.db.models import McpCredential
from app.db.session import session_scope
from app.mcp.errors import McpAuthError

logger = logging.getLogger(__name__)

CREDENTIAL_ID = 1
PENDING_TTL_SECONDS = 600
REFRESH_MARGIN = timedelta(seconds=120)


@dataclass
class _Pending:
    verifier: str
    created: float


class OAuthManager:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._pending: dict[str, _Pending] = {}
        self._metadata: dict | None = None
        self._refresh_lock = asyncio.Lock()

    # ---------- 元数据与注册 ----------

    async def _discover(self) -> dict:
        if self._metadata:
            return self._metadata
        parsed = urlparse(self.settings.mcp_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        async with httpx.AsyncClient(timeout=15) as http:
            prm = await http.get(f"{origin}/.well-known/oauth-protected-resource")
            auth_server = origin
            if prm.status_code == 200:
                servers = prm.json().get("authorization_servers") or []
                if servers:
                    auth_server = servers[0].rstrip("/")
            resp = await http.get(f"{auth_server}/.well-known/oauth-authorization-server")
            resp.raise_for_status()
            self._metadata = resp.json()
        return self._metadata

    async def _ensure_client(self) -> str:
        with session_scope() as db:
            cred = db.get(McpCredential, CREDENTIAL_ID)
            if cred and cred.client_id:
                registered_uris = (cred.client_info or {}).get("redirect_uris") or []
                if self.settings.mcp_oauth_redirect_uri in registered_uris:
                    return cred.client_id
        meta = await self._discover()
        body = {
            "client_name": "chan-stock-analysis",
            "redirect_uris": [self.settings.mcp_oauth_redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": self.settings.mcp_oauth_scope,
        }
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(meta["registration_endpoint"], json=body)
        if resp.status_code >= 400:
            raise McpAuthError(f"动态注册客户端失败: HTTP {resp.status_code} {resp.text[:300]}")
        info = resp.json()
        with session_scope() as db:
            cred = db.get(McpCredential, CREDENTIAL_ID) or McpCredential(id=CREDENTIAL_ID)
            cred.client_id = info["client_id"]
            cred.client_info = info
            db.merge(cred)
        return info["client_id"]

    # ---------- 授权码流程 ----------

    async def build_authorize_url(self) -> str:
        meta = await self._discover()
        client_id = await self._ensure_client()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(32)
        self._gc_pending()
        self._pending[state] = _Pending(verifier=verifier, created=time.time())
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": self.settings.mcp_oauth_redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": self.settings.mcp_oauth_scope,
            "resource": self.settings.mcp_url,
        }
        return f"{meta['authorization_endpoint']}?{urlencode(params)}"

    async def handle_callback(self, code: str, state: str) -> None:
        pending = self._pending.pop(state, None)
        if pending is None or time.time() - pending.created > PENDING_TTL_SECONDS:
            raise McpAuthError("授权状态已失效，请重新发起授权")
        meta = await self._discover()
        client_id = await self._ensure_client()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.mcp_oauth_redirect_uri,
            "client_id": client_id,
            "code_verifier": pending.verifier,
            "resource": self.settings.mcp_url,
        }
        await self._request_token(meta["token_endpoint"], data, source="oauth")

    async def _request_token(self, token_url: str, data: dict, source: str) -> None:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(token_url, data=data)
        if resp.status_code >= 400:
            raise McpAuthError(f"获取 token 失败: HTTP {resp.status_code} {resp.text[:300]}")
        tok = resp.json()
        self._save_token(tok, source=source)

    def _save_token(self, tok: dict, source: str) -> None:
        expires_in = tok.get("expires_in")
        with session_scope() as db:
            cred = db.get(McpCredential, CREDENTIAL_ID) or McpCredential(id=CREDENTIAL_ID)
            cred.access_token = tok["access_token"]
            if tok.get("refresh_token"):
                cred.refresh_token = tok["refresh_token"]
            cred.token_type = tok.get("token_type", "Bearer")
            cred.scope = tok.get("scope")
            cred.expires_at = datetime.now() + timedelta(seconds=int(expires_in)) if expires_in else None
            cred.source = source
            db.merge(cred)

    def save_manual_token(self, access_token: str, expires_in: int | None = None) -> None:
        self._save_token(
            {"access_token": access_token.strip(), "expires_in": expires_in, "token_type": "Bearer"},
            source="manual",
        )

    def _gc_pending(self) -> None:
        now = time.time()
        for key in [k for k, v in self._pending.items() if now - v.created > PENDING_TTL_SECONDS]:
            self._pending.pop(key, None)

    # ---------- 取用与续期 ----------

    async def get_access_token(self, force_refresh: bool = False) -> str:
        with session_scope() as db:
            cred = db.get(McpCredential, CREDENTIAL_ID)
            snapshot = (
                None
                if cred is None
                else (cred.access_token, cred.refresh_token, cred.expires_at, cred.client_id)
            )
        if snapshot is None or not snapshot[0]:
            raise McpAuthError("尚未授权腾讯自选股，请在设置页完成授权")
        access, refresh, expires_at, client_id = snapshot
        expiring = expires_at is not None and expires_at - REFRESH_MARGIN <= datetime.now()
        if not force_refresh and not expiring:
            return access
        if not refresh or not client_id:
            if force_refresh or (expires_at and expires_at <= datetime.now()):
                raise McpAuthError("token 已过期且无法续期，请重新授权")
            return access
        async with self._refresh_lock:
            meta = await self._discover()
            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": client_id,
                "resource": self.settings.mcp_url,
            }
            try:
                await self._request_token(meta["token_endpoint"], data, source="oauth")
            except McpAuthError:
                logger.warning("refresh_token 续期失败")
                raise McpAuthError("token 续期失败，请重新授权") from None
        with session_scope() as db:
            return db.get(McpCredential, CREDENTIAL_ID).access_token

    def status(self) -> dict:
        with session_scope() as db:
            cred = db.get(McpCredential, CREDENTIAL_ID)
            if cred is None or not cred.access_token:
                return {"authorized": False, "client_registered": bool(cred and cred.client_id)}
            return {
                "authorized": True,
                "source": cred.source,
                "scope": cred.scope,
                "expires_at": cred.expires_at.isoformat() if cred.expires_at else None,
                "has_refresh_token": bool(cred.refresh_token),
                "updated_at": cred.updated_at.isoformat() if cred.updated_at else None,
            }

    def revoke_local(self) -> None:
        with session_scope() as db:
            cred = db.get(McpCredential, CREDENTIAL_ID)
            if cred:
                cred.access_token = None
                cred.refresh_token = None
                cred.expires_at = None


oauth_manager = OAuthManager()
