"""写回腾讯自选股 App：推荐股同步到专用自选分组；持仓止损价、目标价同步为股价提醒。

- portfolio_watchlist_remove 是整个自选列表层面的软删除，没有分组参数。
  所以只删除仅存在于推荐分组里的股票，你在其他分组里也有的股票不删。
- portfolio_tips_set 是全量覆盖语义，每次先查出现有提醒，保留其他字段再写回；
  首次覆盖某只股票的 low/high 时记下原值，平仓后恢复。
"""

import logging

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import AppSyncLog, AppSyncState, PositionPlan
from app.db.session import session_scope
from app.mcp.client import McpSession
from app.providers.parsing import find_records, normalize_code, pick

logger = logging.getLogger(__name__)
settings = get_settings()

OPEN_STATUSES = ("Initial", "Breakeven", "Trailing", "Reduced")
TIP_FIELDS = ("high", "low", "updown", "pdrhigh", "pdrlow")


def _get_state(key: str, default=None):
    with session_scope() as db:
        st = db.get(AppSyncState, key)
        return st.value if st and st.value is not None else default


def _set_state(key: str, value) -> None:
    with session_scope() as db:
        st = db.get(AppSyncState, key)
        if st is None:
            db.add(AppSyncState(key=key, value=value))
        else:
            st.value = value


def _log(action: str, ok: bool, code: str | None = None, message: str | None = None, detail: dict | None = None) -> None:
    with session_scope() as db:
        db.add(AppSyncLog(action=action, code=code, ok=ok, message=(message or "")[:1000], detail=detail))


def is_enabled() -> bool:
    if settings.data_provider == "mock":
        return False
    return bool(_get_state("enabled", settings.app_sync_enabled))


def set_enabled(enabled: bool) -> None:
    _set_state("enabled", enabled)


def _fmt(v: float | None) -> str:
    return "" if v is None else f"{v:.2f}"


# ---------- 推荐分组 ----------


async def _ensure_group(sess: McpSession) -> str:
    state = _get_state("recommend_group", {}) or {}
    data = await sess.call("portfolio_watchlist_groups", {})
    groups = find_records(data, prefer=("groups", "list"))
    for g in groups:
        if not isinstance(g, dict):
            continue
        gid = str(pick(g, "group_id", "id", "groupId", default=""))
        name = pick(g, "name", "group_name", "groupName")
        if gid and (gid == state.get("group_id") or name == settings.app_sync_group_name) and not gid.startswith("tmp"):
            if state.get("group_id") != gid:
                _set_state("recommend_group", {**state, "group_id": gid})
            return gid
    created = await sess.call("portfolio_group_add", {"name": settings.app_sync_group_name})
    gid = str(pick(created if isinstance(created, dict) else {}, "group_id", "id", "groupId", default=""))
    if not gid:
        raise RuntimeError(f"创建自选分组失败: {created}")
    _set_state("recommend_group", {"group_id": gid, "codes": []})
    _log("group_add", True, message=f"创建分组 {settings.app_sync_group_name}", detail={"group_id": gid})
    return gid


async def _codes_in_other_groups(sess: McpSession, group_id: str) -> set[str]:
    data = await sess.call("portfolio_watchlist_groups", {})
    others: set[str] = set()
    for g in find_records(data, prefer=("groups", "list")):
        if not isinstance(g, dict):
            continue
        gid = str(pick(g, "group_id", "id", "groupId", default=""))
        name = str(pick(g, "name", "group_name", default=""))
        if not gid or gid == group_id or name in ("全部", "待删除") or gid.startswith("tmp"):
            continue
        members = await sess.call("portfolio_watchlist", {"group": gid, "limit": 500})
        for r in find_records(members):
            c = normalize_code(pick(r, "code", "symbol")) if isinstance(r, dict) else None
            if c:
                others.add(c)
    return others


async def sync_recommend_group(codes: list[str]) -> dict:
    if not is_enabled():
        return {"skipped": True}
    codes = [c for c in codes if c.startswith(("sh", "sz", "bj"))]
    sess = McpSession()
    try:
        gid = await _ensure_group(sess)
        state = _get_state("recommend_group", {}) or {}
        old = set(state.get("codes") or [])
        new = set(codes)
        to_add = sorted(new - old)
        to_remove = sorted(old - new)
        if to_add:
            try:
                await sess.call("portfolio_watchlist_batch_add", {"codes": ",".join(to_add), "group_id": gid})
                _log("watchlist_add", True, ",".join(to_add)[:255], f"加入 {len(to_add)} 只")
            except Exception as exc:
                _log("watchlist_add", False, ",".join(to_add)[:255], str(exc))
                raise
        removed, kept = [], []
        if to_remove:
            protected = await _codes_in_other_groups(sess, gid)
            removed = [c for c in to_remove if c not in protected]
            kept = [c for c in to_remove if c in protected]
            if removed:
                try:
                    await sess.call("portfolio_watchlist_remove", {"codes": ",".join(removed)})
                    _log("watchlist_remove", True, ",".join(removed)[:255], f"移出 {len(removed)} 只（进入 App 待删除分组）")
                except Exception as exc:
                    _log("watchlist_remove", False, ",".join(removed)[:255], str(exc))
            if kept:
                _log("watchlist_keep", True, ",".join(kept)[:255], "这些股票也在你的其他分组里，未删除")
        _set_state("recommend_group", {"group_id": gid, "codes": sorted(new | set(kept))})
        return {"group_id": gid, "added": len(to_add), "removed": len(removed), "kept": len(kept)}
    finally:
        await sess.close()


# ---------- 股价提醒 ----------


def _tip_map(data) -> dict[str, dict]:
    out = {}
    for r in find_records(data, prefer=("tips", "list")):
        if isinstance(r, dict):
            c = normalize_code(pick(r, "code", "symbol"))
            if c:
                out[c] = r
    return out


def _types_from_tip(code: str, tip: dict) -> str:
    hk = code.startswith("hk")
    types = []
    if str(tip.get("notice", "0")) == "1":
        types.append("2020" if hk else "2010")
    if str(tip.get("research", "0")) == "1":
        types.append("2021" if hk else "2011")
    return ",".join(types)


def _tip_args(code: str, existing: dict, low: str, high: str) -> dict:
    args = {"code": code}
    for f in TIP_FIELDS:
        v = existing.get(f)
        args[f] = "" if v in (None, 0, "0", "0.00") else str(v)
    args["low"], args["high"] = low, high
    t = _types_from_tip(code, existing)
    if t:
        args["type"] = t
    return args


async def sync_price_alerts() -> dict:
    if not is_enabled():
        return {"skipped": True}
    with session_scope() as db:
        plans = db.execute(select(PositionPlan).where(PositionPlan.status.in_(OPEN_STATUSES), PositionPlan.remaining_qty > 0)).scalars().all()
        desired = {p.code: {"low": _fmt(p.current_stop), "high": _fmt(p.target1)} for p in plans if p.code.startswith(("sh", "sz"))}
    state: dict = _get_state("price_alerts", {}) or {}
    changed = [c for c, v in desired.items() if state.get(c, {}).get("low") != v["low"] or state.get(c, {}).get("high") != v["high"]]
    closed = [c for c in state if c not in desired]
    if not changed and not closed:
        return {"updated": 0, "restored": 0}
    sess = McpSession()
    updated = restored = 0
    try:
        tips = _tip_map(await sess.call("portfolio_tips_query", {}))
        for code in changed:
            existing = tips.get(code, {})
            entry = state.get(code) or {"orig_low": _norm_tip(existing.get("low")), "orig_high": _norm_tip(existing.get("high"))}
            args = _tip_args(code, existing, desired[code]["low"], desired[code]["high"])
            try:
                await sess.call("portfolio_tips_set", args)
                state[code] = {**entry, **desired[code]}
                updated += 1
                _log("tips_set", True, code, f"止损提醒 {args['low']}，目标提醒 {args['high']}", {"args": args})
            except Exception as exc:
                _log("tips_set", False, code, str(exc), {"args": args})
        for code in closed:
            entry = state.get(code, {})
            existing = tips.get(code, {})
            args = _tip_args(code, existing, entry.get("orig_low", ""), entry.get("orig_high", ""))
            try:
                await sess.call("portfolio_tips_set", args)
                state.pop(code, None)
                restored += 1
                _log("tips_restore", True, code, "平仓后恢复原有提醒", {"args": args})
            except Exception as exc:
                _log("tips_restore", False, code, str(exc), {"args": args})
    finally:
        await sess.close()
        _set_state("price_alerts", state)
    return {"updated": updated, "restored": restored}


def _norm_tip(v) -> str:
    return "" if v in (None, "", 0, "0", "0.00") else str(v)


def recent_logs(limit: int = 100) -> list[dict]:
    with session_scope() as db:
        rows = db.execute(select(AppSyncLog).order_by(AppSyncLog.id.desc()).limit(limit)).scalars().all()
        return [
            {"id": r.id, "action": r.action, "code": r.code, "ok": r.ok, "message": r.message, "created_at": r.created_at.isoformat()}
            for r in rows
        ]


def status() -> dict:
    return {
        "enabled": is_enabled(),
        "mock_mode": settings.data_provider == "mock",
        "group": _get_state("recommend_group", {}),
        "price_alerts": _get_state("price_alerts", {}),
    }
