"""写回腾讯自选股 App：推荐股同步到专用自选分组；持仓止损价、目标价同步为股价提醒。

- portfolio_watchlist_remove 只是把股票加进自建的「待删除」分组，原分组里仍然保留；
  portfolio_watchlist_move 只能在自建分组之间移动，不能移入「沪深」等系统分组。
  所以推荐结束时：加入前就在你自选里的股票保留不动；也在你其他自建分组里的，从推荐分组移过去；
  其余是系统加的，从推荐分组移到「待删除」。「全部」「沪深」等系统分组会自动包含所有 A 股自选，不算你的分组。
- 以推荐分组的实际成员为准，只处理系统加过的股票（state.added），你手动加进推荐分组的不动。
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


SYSTEM_GROUP_TYPES = {"1", "2"}  # 1：全部；2：沪深、港股、美股、基金等按市场自动归类的分组
ALL_GROUP_ID = "1"
TRASH_GROUP_NAME = "待删除"


async def _group_id_by_name(sess: McpSession, name: str) -> str | None:
    data = await sess.call("portfolio_watchlist_groups", {})
    for g in find_records(data, prefer=("groups", "list")):
        if isinstance(g, dict) and pick(g, "name", "group_name") == name:
            return str(pick(g, "group_id", "id", "groupId", default="")) or None
    return None


async def _members(sess: McpSession, group_id: str) -> set[str]:
    out: set[str] = set()
    for offset in range(0, 2000, 200):
        data = await sess.call("portfolio_watchlist", {"group": group_id, "limit": 200, "offset": offset})
        rows = [r for r in find_records(data) if isinstance(r, dict)]
        out.update(c for c in (normalize_code(pick(r, "code", "symbol")) for r in rows) if c)
        if len(rows) < 200:
            break
    return out


async def _custom_groups(sess: McpSession, group_id: str) -> dict[str, set[str]]:
    """推荐分组之外、你自建的分组及其成员。"""
    data = await sess.call("portfolio_watchlist_groups", {})
    out: dict[str, set[str]] = {}
    for g in find_records(data, prefer=("groups", "list")):
        if not isinstance(g, dict):
            continue
        gid = str(pick(g, "group_id", "id", "groupId", default=""))
        gtype = str(pick(g, "groupType", "group_type", "type", default=""))
        name = str(pick(g, "name", "group_name", default=""))
        if not gid or gid == group_id or gid.startswith("tmp") or gtype in SYSTEM_GROUP_TYPES or name in ("全部", "待删除"):
            continue
        out[gid] = await _members(sess, gid)
    return out


def plan_group_removal(to_remove: list[str], user_owned: set[str], custom: dict[str, set[str]]) -> tuple[list[str], dict[str, str], list[str]]:
    """返回（移入待删除的，移到自建分组的 {代码: 分组}，保留不动的）。"""
    removed, moved, kept = [], {}, []
    for code in to_remove:
        if code in user_owned:
            kept.append(code)
            continue
        target = next((gid for gid, members in custom.items() if code in members), None)
        if target:
            moved[code] = target
        else:
            removed.append(code)
    return removed, moved, kept


async def _move_to_trash(sess: McpSession, group_id: str, codes: list[str]) -> tuple[list[str], list[str]]:
    """把股票从推荐分组移到「待删除」，返回（成功，失败）。分组不存在时先用删除接口让腾讯创建它。"""
    trash = await _group_id_by_name(sess, TRASH_GROUP_NAME)
    if trash is None:
        await sess.call("portfolio_watchlist_remove", {"code": codes[0]})
        trash = await _group_id_by_name(sess, TRASH_GROUP_NAME)
    if trash is None:
        _log("watchlist_remove", False, ",".join(codes)[:255], "找不到「待删除」分组")
        return [], codes
    ok, failed = [], []
    for code in codes:
        try:
            await sess.call("portfolio_watchlist_move", {"code": code, "from": group_id, "to": trash, "retain": False})
            ok.append(code)
        except Exception as exc:  # noqa: BLE001
            failed.append(code)
            _log("watchlist_remove", False, code, str(exc))
    return ok, failed


async def sync_recommend_group(codes: list[str]) -> dict:
    if not is_enabled():
        return {"skipped": True}
    codes = [c for c in codes if c.startswith(("sh", "sz", "bj"))]
    sess = McpSession()
    try:
        gid = await _ensure_group(sess)
        state = _get_state("recommend_group", {}) or {}
        actual = await _members(sess, gid)
        user_owned = set(state.get("user_owned") or [])
        # 旧版本没有记录 added，当时分组里的股票经确认都是系统加的
        added = set(state["added"]) if "added" in state else actual | set(state.get("codes") or [])
        new = set(codes)
        to_add = sorted(new - actual)
        if to_add:
            # 加入前已在自选里的，记为你原有的自选，推荐结束后不删除
            existing = await _members(sess, ALL_GROUP_ID)
            pre = set(to_add) & existing
            if pre:
                user_owned |= pre
                _log("watchlist_owned", True, ",".join(sorted(pre))[:255], f"{len(pre)} 只本来就在你的自选里，推荐结束后保留")
            try:
                await sess.call("portfolio_watchlist_batch_add", {"codes": ",".join(to_add), "group_id": gid})
                _log("watchlist_add", True, ",".join(to_add)[:255], f"加入 {len(to_add)} 只", detail={"codes": to_add})
            except Exception as exc:
                _log("watchlist_add", False, ",".join(to_add)[:255], str(exc))
                raise
            added |= set(to_add) - pre
        to_remove = sorted((actual & added) - new)
        removed, moved, kept = [], {}, []
        if to_remove:
            removed, moved, kept = plan_group_removal(to_remove, user_owned, await _custom_groups(sess, gid))
            for code, target in moved.items():
                try:
                    await sess.call("portfolio_watchlist_move", {"code": code, "from": gid, "to": target, "retain": False})
                except Exception as exc:
                    kept.append(code)
                    _log("watchlist_move", False, code, str(exc))
            if moved:
                _log("watchlist_move", True, ",".join(c for c in moved if c not in kept)[:255], "也在你的自建分组里，只移出推荐分组")
            if removed:
                removed, failed = await _move_to_trash(sess, gid, removed)
                kept.extend(failed)
                if removed:
                    _log("watchlist_remove", True, ",".join(removed)[:255], f"移出 {len(removed)} 只（移到 App「待删除」分组，可恢复）",
                         detail={"codes": removed})
            if kept:
                _log("watchlist_keep", True, ",".join(kept)[:255], "你原有的自选或移动失败，保留在推荐分组中")
        # 保留下来的股票不再记为推荐成员，避免每天重复处理
        _set_state("recommend_group", {"group_id": gid, "codes": sorted(new), "user_owned": sorted(user_owned),
                                       "added": sorted(added - set(removed) - set(moved)),
                                       "kept": sorted(set(state.get("kept") or []) | set(kept))})
        return {"group_id": gid, "added": len(to_add), "removed": len(removed), "moved": len(moved), "kept": len(kept)}
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
