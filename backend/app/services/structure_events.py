"""自选股结构异动：和上一个交易日的结构状态比较，记录状态切换、买点出现/确认/失效、跌破或突破关键价位、形成新中枢。

「我的自选」= 本地自选 + 腾讯 App 里你自己加的股票（不含系统为候选分组加入的）；App 自选只读取、不写入。
"""

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert

from app.analysis.structure import STATE_NAMES, classify
from app.db.models import StockBasic, StructureEvent, StructureStateDaily, Watchlist
from app.db.session import session_scope
from app.services.chan_service import analyze_stock
from app.services.jobs import JobContext
from app.services.notify import notify
from app.services.runtime_state import get_state, set_state
from app.services.structure_state import state_row

logger = logging.getLogger(__name__)

WATCH_CACHE_KEY = "my_watch_codes"
EVENT_NAMES = {
    "state_change": "结构切换", "signal_new": "出现买点", "signal_confirmed": "买点确认", "signal_invalid": "买点失效",
    "break_lower": "跌破下方价位", "break_upper": "突破上方价位", "new_zhongshu": "形成新中枢",
}
IMPORTANT = {"signal_invalid", "break_lower"}


async def my_watch_codes() -> dict:
    """本地自选 + App 自选（去掉系统加入的）；App 读取失败时沿用上次的名单。"""
    with session_scope() as db:
        local = set(db.execute(select(Watchlist.code)).scalars())
    app_codes: set[str] = set()
    try:
        from app.mcp.client import McpSession
        from app.services import app_sync

        sess = McpSession()
        try:
            app_codes = await app_sync._members(sess, app_sync.ALL_GROUP_ID)
        finally:
            await sess.close()
        state = app_sync._get_state("recommend_group", {}) or {}
        app_codes -= set(state.get("added") or [])
        set_state(WATCH_CACHE_KEY, sorted(app_codes))
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 App 自选失败，沿用上次名单: %s", exc)
        app_codes = set(get_state(WATCH_CACHE_KEY) or [])
    codes = sorted(c for c in local | app_codes if c[:2] in ("sh", "sz"))
    return {"codes": codes, "local": len(local), "app": len(app_codes)}


def _ensure_state(code: str, d: date) -> StructureStateDaily | None:
    """当天扫描没覆盖到的自选股（例如 ST、流动性不足）补算一次结构状态。"""
    with session_scope() as db:
        row = db.get(StructureStateDaily, (code, d))
        if row is not None:
            db.expunge(row)
            return row
    a = analyze_stock(code, end=d)
    if a is None or not a.day.dates or a.day.dates[-1] != d:
        return None
    r = state_row(code, d, classify(a.day_state or a.day), classify(a.week) if a.week else None)
    with session_scope() as db:
        db.execute(insert(StructureStateDaily).values([r]).prefix_with("IGNORE"))
        row = db.get(StructureStateDaily, (code, d))
        db.expunge(row)
        return row


def _prev_state(code: str, d: date) -> StructureStateDaily | None:
    with session_scope() as db:
        row = db.execute(select(StructureStateDaily).where(StructureStateDaily.code == code, StructureStateDaily.trade_date < d)
                         .order_by(StructureStateDaily.trade_date.desc()).limit(1)).scalars().first()
        if row is not None:
            db.expunge(row)
        return row


def _zs(levels: list | None) -> tuple[float | None, float | None]:
    lv = {x.get("kind"): x.get("price") for x in (levels or []) if isinstance(x, dict)}
    return lv.get("zd"), lv.get("zg")


def _level_name(levels: list | None, price: float | None) -> str:
    for x in levels or []:
        if isinstance(x, dict) and price is not None and x.get("price") is not None and abs(x["price"] - price) < 1e-6:
            return x.get("name") or "关键价位"
    return "关键价位"


def detect(prev: StructureStateDaily | None, cur: StructureStateDaily) -> list[dict]:
    """比较两天的结构状态，返回异动列表 [{type, level, title, detail}]。"""
    if prev is None:
        return []
    out = []
    pname, cname = STATE_NAMES.get(prev.state, prev.state), STATE_NAMES.get(cur.state, cur.state)
    ps, cs = prev.signal or {}, cur.signal or {}
    same_signal = bool(ps and cs and ps.get("type") == cs.get("type") and ps.get("date") == cs.get("date"))
    if cs and not same_signal:
        out.append(("signal_new", f"出现{cs.get('name')}（{cs.get('date')}，{'已确认' if cs.get('confirmed') else '未确认'}）"))
    if same_signal and not ps.get("confirmed") and cs.get("confirmed"):
        out.append(("signal_confirmed", f"{cs.get('name')}（{cs.get('date')}）所在的笔已确认"))
    broke_lower = prev.lower is not None and cur.price is not None and cur.price < prev.lower
    if ps and not cs and broke_lower:
        out.append(("signal_invalid", f"{ps.get('name')}失效：收盘 {cur.price:.2f} 跌破认错位 {prev.lower:.2f}"))
    elif broke_lower:
        out.append(("break_lower", f"收盘 {cur.price:.2f} 跌破{_level_name(prev.levels, prev.lower)} {prev.lower:.2f}"))
    if prev.upper is not None and cur.price is not None and cur.price > prev.upper:
        out.append(("break_upper", f"收盘 {cur.price:.2f} 突破{_level_name(prev.levels, prev.upper)} {prev.upper:.2f}"))
    pzd, pzg = _zs(prev.levels)
    czd, czg = _zs(cur.levels)
    if czd is not None and czg is not None and (pzd, pzg) != (czd, czg) and pzd is not None:
        out.append(("new_zhongshu", f"形成新中枢 {czd:.2f}～{czg:.2f}（原中枢 {pzd:.2f}～{pzg:.2f}）"))
    if prev.state != cur.state and not any(t in ("signal_new", "signal_invalid") for t, _ in out):
        out.append(("state_change", f"结构从「{pname}」变为「{cname}」"))
    detail = {"before": {"state": prev.state, "state_name": pname, "price": prev.price, "upper": prev.upper, "lower": prev.lower,
                         "date": prev.trade_date.isoformat()},
              "after": {"state": cur.state, "state_name": cname, "price": cur.price, "upper": cur.upper, "lower": cur.lower}}
    return [{"type": t, "level": "important" if t in IMPORTANT else "info", "title": title, "detail": detail} for t, title in out]


async def detect_watch_events(ctx: JobContext | None = None, d: date | None = None) -> dict:
    with session_scope() as db:
        d = d or db.scalar(select(func.max(StructureStateDaily.trade_date)))
        names = {}
    if d is None:
        return {"skipped": "还没有结构状态"}
    watch = await my_watch_codes()
    codes = watch["codes"]
    if ctx:
        ctx.update(message=f"自选股结构异动：{len(codes)} 只（本地 {watch['local']}，App {watch['app']}）", force=True)
    with session_scope() as db:
        names = dict(db.execute(select(StockBasic.code, StockBasic.name).where(StockBasic.code.in_(codes))).all()) if codes else {}
    rows = []
    for code in codes:
        try:
            cur = await asyncio.to_thread(_ensure_state, code, d)
            if cur is None:
                continue
            prev = await asyncio.to_thread(_prev_state, code, d)
            for e in detect(prev, cur):
                rows.append({"code": code, "trade_date": d, "event_type": e["type"], "level": e["level"], "title": e["title"][:255], "detail": e["detail"]})
        except Exception:  # noqa: BLE001
            logger.exception("结构异动检测失败 %s", code)
    if rows:
        with session_scope() as db:
            stmt = insert(StructureEvent).values(rows)
            db.execute(stmt.on_duplicate_key_update(level=stmt.inserted.level, title=stmt.inserted.title, detail=stmt.inserted.detail))
    important = [r for r in rows if r["level"] == "important"]
    if important:
        notify("structure_event", f"自选股有 {len(important)} 条重要结构异动",
               "；".join(f"{names.get(r['code'], r['code'])}：{r['title']}" for r in important[:8]), "important", day=d)
    return {"date": d.isoformat(), "watch": len(codes), "events": len(rows), "important": len(important)}


def list_events(days: int = 30, code: str | None = None, level: str | None = None) -> dict:
    since = date.today() - timedelta(days=days)
    with session_scope() as db:
        q = select(StructureEvent).where(StructureEvent.trade_date >= since)
        if code:
            q = q.where(StructureEvent.code == code)
        if level:
            q = q.where(StructureEvent.level == level)
        rows = db.execute(q.order_by(StructureEvent.trade_date.desc(), StructureEvent.level, StructureEvent.id)).scalars().all()
        names = dict(db.execute(select(StockBasic.code, StockBasic.name).where(StockBasic.code.in_({r.code for r in rows}))).all()) if rows else {}
    return {
        "items": [{"id": r.id, "code": r.code, "name": names.get(r.code, r.code), "trade_date": r.trade_date.isoformat(),
                   "event_type": r.event_type, "event_name": EVENT_NAMES.get(r.event_type, r.event_type), "level": r.level,
                   "title": r.title, "detail": r.detail} for r in rows],
        "event_names": EVENT_NAMES, "watch_cached": len(get_state(WATCH_CACHE_KEY) or []),
    }
