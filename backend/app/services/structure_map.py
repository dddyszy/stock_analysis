"""市场与行业结构地图：4 个主要指数和申万一级行业等权指数，每天做和个股同样的结构分类（classify，最近 STATE_BARS 根）。

行业等权指数：行业内每只股票当天的开、高、低、收相对前一天收盘的涨跌幅取等权平均，逐日累乘成行业日 K 线。
"""

import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.mysql import insert

from app.analysis.structure import STATE_BARS, STATE_NAMES, classify
from app.chan.analyzer import analyze
from app.db.models import KlineDaily, StructureMapDaily
from app.db.session import engine, session_scope
from app.providers.base import INDEX_CODES, Bar
from app.services.chan_service import chan_config
from app.services.jobs import JobContext
from app.services.sync import load_bars

logger = logging.getLogger(__name__)

DAILY_CLIP = 0.25
LOOKBACK_DAYS = int(STATE_BARS * 1.6)
# 结构地图的 5 类颜色；买点状态没有位置含义，按现价相对中枢上下沿归类
CATEGORY = {"above_up": "up", "above_pullback": "above", "inside": "inside", "below_rebound": "below", "below_down": "down"}
CATEGORY_NAMES = {"up": "向上离开", "above": "中枢上方", "inside": "中枢内", "below": "中枢下方", "down": "向下离开", "insufficient": "数据不足"}


def category_of(state: str, price: float | None, levels: list | None) -> str:
    if state in CATEGORY:
        return CATEGORY[state]
    lv = {x.get("kind"): x.get("price") for x in (levels or []) if isinstance(x, dict)}
    zg, zd = lv.get("zg"), lv.get("zd")
    if price is None or zg is None or zd is None:
        return "insufficient"
    return "above" if price > zg else "below" if price < zd else "inside"


def industry_bars(end: date, days: int = LOOKBACK_DAYS) -> dict[str, list[Bar]]:
    sql = text(
        "SELECT k.code, k.trade_date, k.open, k.high, k.low, k.close, s.industry FROM kline_daily k "
        "JOIN stock_basic s ON s.code = k.code WHERE s.is_index = 0 AND s.active = 1 AND s.industry IS NOT NULL "
        "AND k.trade_date > :start AND k.trade_date <= :end"
    )
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"start": end - timedelta(days=days), "end": end})
    if df.empty:
        return {}
    df = df.sort_values(["code", "trade_date"])
    prev = df.groupby("code")["close"].shift(1)
    for col in ("open", "high", "low", "close"):
        df[f"r_{col}"] = (df[col] / prev - 1).clip(-DAILY_CLIP, DAILY_CLIP)
    df = df.dropna(subset=["r_close"])
    daily = df.groupby(["industry", "trade_date"])[["r_open", "r_high", "r_low", "r_close"]].mean()
    out: dict[str, list[Bar]] = {}
    for ind, g in daily.groupby(level=0):
        level = 1000.0
        bars = []
        for d, r in g.droplevel(0).sort_index().iterrows():
            o, c = level * (1 + r.r_open), level * (1 + r.r_close)
            h = max(level * (1 + r.r_high), o, c)
            lo = min(level * (1 + r.r_low), o, c)
            dt = d.date() if hasattr(d, "date") and not isinstance(d, date) else d
            bars.append(Bar(dt=dt, open=o, high=h, low=lo, close=c))
            level = c
        out[str(ind)] = bars
    return out


def _row(kind: str, key: str, name: str, bars: list[Bar], cfg) -> dict | None:
    if len(bars) < 60:
        return None
    st = classify(analyze(bars[-STATE_BARS:], "day", cfg))
    ret5 = (bars[-1].close / bars[-6].close - 1) * 100 if len(bars) > 5 else None
    return {"kind": kind, "key": key, "trade_date": bars[-1].dt, "name": name, "state": st.key, "price": st.price,
            "upper": st.upper, "lower": st.lower, "ret5": round(ret5, 2) if ret5 is not None else None, "levels": st.levels}


def compute_structure_map(ctx: JobContext | None = None) -> dict:
    with session_scope() as db:
        end = db.scalar(select(func.max(KlineDaily.trade_date)))
    if end is None:
        return {"skipped": "没有日线"}
    cfg = chan_config()
    rows = []
    for code, name in INDEX_CODES.items():
        r = _row("index", code, name, load_bars(KlineDaily, code, STATE_BARS, end=end), cfg)
        if r:
            rows.append(r)
    for ind, bars in industry_bars(end).items():
        r = _row("industry", ind, ind, bars, cfg)
        if r:
            rows.append(r)
    rows = [r for r in rows if r["trade_date"] == end]
    with session_scope() as db:
        db.execute(delete(StructureMapDaily).where(StructureMapDaily.trade_date == end))
        if rows:
            db.execute(insert(StructureMapDaily).values(rows))
    if ctx:
        ctx.update(message=f"结构地图：指数 {sum(r['kind'] == 'index' for r in rows)} 个，行业 {sum(r['kind'] == 'industry' for r in rows)} 个", force=True)
    return {"date": end.isoformat(), "items": len(rows)}


def structure_map() -> dict:
    with session_scope() as db:
        d = db.scalar(select(func.max(StructureMapDaily.trade_date)))
        if d is None:
            return {"date": None, "items": [], "categories": CATEGORY_NAMES}
        prev_d = db.scalar(select(func.max(StructureMapDaily.trade_date)).where(StructureMapDaily.trade_date < d))
        cur = db.execute(select(StructureMapDaily).where(StructureMapDaily.trade_date == d)).scalars().all()
        prev = {(r.kind, r.key): r.state for r in db.execute(select(StructureMapDaily).where(StructureMapDaily.trade_date == prev_d)).scalars()} if prev_d else {}
    items = []
    for r in cur:
        p = prev.get((r.kind, r.key))
        items.append({
            "kind": r.kind, "key": r.key, "name": r.name, "state": r.state, "state_name": STATE_NAMES.get(r.state, r.state),
            "category": category_of(r.state, r.price, r.levels), "price": r.price, "upper": r.upper, "lower": r.lower, "ret5": r.ret5,
            "prev_state": p, "prev_state_name": STATE_NAMES.get(p, p) if p else None, "changed": bool(p and p != r.state),
            "up_dist": (r.upper / r.price - 1) * 100 if r.upper and r.price else None,
            "low_dist": (r.lower / r.price - 1) * 100 if r.lower and r.price else None,
        })
    order = list(CATEGORY_NAMES)
    items.sort(key=lambda x: (x["kind"] != "index", order.index(x["category"]), -(x["ret5"] or 0)))
    return {"date": d.isoformat(), "prev_date": prev_d.isoformat() if prev_d else None, "items": items, "categories": CATEGORY_NAMES}
