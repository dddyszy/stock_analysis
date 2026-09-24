"""市场环境：指数缠论状态 + 市场宽度 + 成交额 + 板块强弱 → 市场温度与建议总仓位上限。

宽度和板块强弱用本地全市场日线计算，保证历史可复现；MCP 的实时涨跌分布只作为补充展示。
"""

import asyncio
import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.dialects.mysql import insert

from app.chan import analyze, combine
from app.db.models import KlineDaily, KlineWeekly, MarketEnvDaily, StockBasic
from app.db.session import engine, session_scope
from app.providers.base import INDEX_CODES, DataProvider
from app.services.ashare_rules import limit_pct, volume_lot
from app.services.strategy_config import get_active_params
from app.services.sync import load_bars

logger = logging.getLogger(__name__)

REGIME_NAMES = {"strong": "强势", "neutral": "震荡", "weak": "弱势"}


def index_state(code: str, end: date | None = None) -> dict | None:
    daily = load_bars(KlineDaily, code, 600, end=end)
    weekly = load_bars(KlineWeekly, code, 300, end=end)
    if len(daily) < 60:
        return None
    d = analyze(daily, "day")
    w = analyze(weekly, "week") if len(weekly) >= 30 else None
    view = combine(d, w)
    recent = d.recent_signals(10)
    score = 0.0
    if view.week_uptrend:
        score += 1.0
    elif view.week_walk_type == "down_trend" and view.week_position == "below":
        score -= 1.0
    pos = d.summary["position"]
    score += {"above": 0.5, "below": -0.5}.get(pos, 0.0)
    if d.summary["walk_type"] == "up_trend":
        score += 0.5
    elif d.summary["walk_type"] == "down_trend":
        score -= 0.5
    last_sig = recent[-1] if recent else None
    if last_sig is not None:
        score += 0.5 if last_sig.is_buy else -0.5
    if d.summary["macd_above_zero"]:
        score += 0.25
    else:
        score -= 0.25
    closes = d.closes
    chg20 = (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 21 else None
    return {
        "code": code,
        "name": INDEX_CODES.get(code, code),
        "close": closes[-1],
        "change_pct": round((closes[-1] / closes[-2] - 1) * 100, 2) if len(closes) > 1 else None,
        "change_20d": round(chg20, 2) if chg20 is not None else None,
        "day_walk_type": d.summary["walk_type"],
        "day_position": pos,
        "last_bi_direction": d.summary["last_bi_direction"],
        "week": view.to_dict(),
        "recent_signal": {"type": last_sig.type, "date": last_sig.dt.isoformat()} if last_sig else None,
        "score": round(score, 2),  # -3 ~ 3
    }


def _load_recent_frame(end: date, days: int = 100) -> pd.DataFrame:
    start = end - timedelta(days=days)
    sql = text(
        "SELECT k.code, k.trade_date, k.close, k.volume, s.industry, s.is_st "
        "FROM kline_daily k JOIN stock_basic s ON s.code = k.code "
        "WHERE s.is_index = 0 AND s.active = 1 AND k.trade_date BETWEEN :start AND :end"
    )
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params={"start": start, "end": end})


def breadth_from_db(end: date) -> tuple[dict, list[dict]]:
    df = _load_recent_frame(end)
    if df.empty:
        return {}, []
    df = df.sort_values(["code", "trade_date"])
    g = df.groupby("code")["close"]
    df["prev"] = g.shift(1)
    df["ma20"] = g.transform(lambda s: s.rolling(20).mean())
    df["ma60"] = g.transform(lambda s: s.rolling(60).mean())
    df["ret"] = df["close"] / df["prev"] - 1
    df["ret5"] = g.transform(lambda s: s / s.shift(5) - 1)
    df["ret20"] = g.transform(lambda s: s / s.shift(20) - 1)
    last_day = df["trade_date"].max()
    today = df[df["trade_date"] == last_day]
    valid = today.dropna(subset=["prev"])
    up = int((valid["ret"] > 0.0001).sum())
    down = int((valid["ret"] < -0.0001).sum())
    total = max(len(valid), 1)
    # 按板块涨跌停幅度判断（主板 10%、ST 5%、创业板和科创板 20%），留 0.5 个百分点容差
    if len(valid):
        pct = pd.Series([limit_pct(c, bool(st)) for c, st in zip(valid["code"], valid["is_st"])], index=valid.index)
        limit_up = int((valid["ret"] >= pct - 0.005).sum())
        limit_down = int((valid["ret"] <= -pct + 0.005).sum())
    else:
        limit_up = limit_down = 0
    above20 = float((today["close"] > today["ma20"]).sum()) / max(int(today["ma20"].notna().sum()), 1)
    above60 = float((today["close"] > today["ma60"]).sum()) / max(int(today["ma60"].notna().sum()), 1)
    # 公开接口的历史 K 线没有成交额，用成交量 × 收盘价估算全市场成交额
    lots = df["code"].map(volume_lot)
    daily_vol = (df["volume"].fillna(0) * df["close"] * lots).groupby(df["trade_date"]).sum().sort_index()
    vol_today = float(daily_vol.iloc[-1]) if len(daily_vol) else 0.0
    vol_ma20 = float(daily_vol.iloc[-21:-1].mean()) if len(daily_vol) > 21 else vol_today
    breadth = {
        "date": last_day.isoformat() if hasattr(last_day, "isoformat") else str(last_day),
        "up": up,
        "down": down,
        "flat": total - up - down,
        "up_ratio": round(up / total, 4),
        "limit_up": limit_up,
        "limit_down": limit_down,
        "above_ma20": round(above20, 4),
        "above_ma60": round(above60, 4),
        "volume": vol_today,
        "amount_ratio_ma20": round(vol_today / vol_ma20, 3) if vol_ma20 else None,
    }
    sectors = []
    if "industry" in today:
        agg = today.dropna(subset=["industry"]).groupby("industry").agg(
            count=("code", "count"),
            ret1=("ret", "mean"),
            ret5=("ret5", "mean"),
            ret20=("ret20", "mean"),
        )
        above = today.assign(a20=today["close"] > today["ma20"]).groupby("industry")["a20"].mean()
        for ind, row in agg.iterrows():
            sectors.append(
                {
                    "industry": ind,
                    "count": int(row["count"]),
                    "ret1": round(float(row["ret1"]) * 100, 2) if pd.notna(row["ret1"]) else None,
                    "ret5": round(float(row["ret5"]) * 100, 2) if pd.notna(row["ret5"]) else None,
                    "ret20": round(float(row["ret20"]) * 100, 2) if pd.notna(row["ret20"]) else None,
                    "above_ma20": round(float(above.get(ind, 0)), 3),
                }
            )
        ranked = sorted(sectors, key=lambda s: (s["ret20"] or -999) * 0.6 + (s["ret5"] or -999) * 0.4, reverse=True)
        n = max(len(ranked), 1)
        for i, s in enumerate(ranked):
            s["rank"] = i + 1
            s["strength"] = round(100 * (1 - i / n), 1)
        sectors = ranked
    return breadth, sectors


def score_environment(indices: list[dict], breadth: dict) -> tuple[float, dict]:
    idx_scores = [i["score"] for i in indices if i]
    idx_part = (sum(idx_scores) / len(idx_scores) + 3) / 6 * 100 if idx_scores else 50.0
    if breadth:
        b = breadth
        limit_total = b["limit_up"] + b["limit_down"]
        limit_part = b["limit_up"] / limit_total if limit_total else 0.5
        breadth_part = float(b["up_ratio"] * 0.3 + b["above_ma20"] * 0.3 + b["above_ma60"] * 0.3 + limit_part * 0.1) * 100
        ratio = b.get("amount_ratio_ma20") or 1.0
        volume_part = max(0.0, min(100.0, 50 + (ratio - 1) * 100))
    else:
        breadth_part, volume_part = 50.0, 50.0
    score = idx_part * 0.5 + breadth_part * 0.35 + volume_part * 0.15
    return round(score, 1), {
        "index_part": round(idx_part, 1),
        "breadth_part": round(breadth_part, 1),
        "volume_part": round(volume_part, 1),
    }


def regime_of(score: float) -> str:
    if score >= 60:
        return "strong"
    if score >= 40:
        return "neutral"
    return "weak"


async def compute_market_env(provider: DataProvider | None = None, end: date | None = None) -> dict:
    with session_scope() as db:
        latest = db.scalar(
            select(KlineDaily.trade_date).where(KlineDaily.code.in_(list(INDEX_CODES))).order_by(KlineDaily.trade_date.desc()).limit(1)
        )
        has_stocks = db.scalar(select(StockBasic.code).where(StockBasic.is_index.is_(False)).limit(1))
    end = end or latest or date.today()
    indices = [s for s in (index_state(c, end) for c in INDEX_CODES) if s]
    breadth, sectors = breadth_from_db(end) if has_stocks else ({}, [])
    score, parts = score_environment(indices, breadth)
    regime = regime_of(score)
    params = get_active_params()
    cap = float(params["regime_caps"][regime])
    detail: dict = {"parts": parts, "regime_name": REGIME_NAMES[regime]}
    if provider is not None:
        try:
            live = await asyncio.wait_for(provider.breadth(), timeout=60)
            detail["live_breadth"] = {
                "up": live.up, "down": live.down, "limit_up": live.limit_up, "limit_down": live.limit_down, "amount": live.amount
            }
        except Exception as exc:
            detail["live_breadth_error"] = str(exc)[:200]
        try:
            detail["tencent_overview"] = await asyncio.wait_for(provider.market_overview(), timeout=60)
        except Exception as exc:
            detail["tencent_overview_error"] = str(exc)[:200]
        try:
            ranking = await asyncio.wait_for(provider.sector_ranking(30), timeout=60)
            detail["live_sector_ranking"] = [{"code": s.code, "name": s.name, "change_pct": s.change_pct} for s in ranking]
        except Exception as exc:
            detail["live_sector_error"] = str(exc)[:200]
    row = {
        "trade_date": end,
        "score": score,
        "regime": regime,
        "position_cap": cap,
        "index_state": {"items": indices},
        "breadth": breadth,
        "sectors": sectors,
        "detail": detail,
    }
    with session_scope() as db:
        stmt = insert(MarketEnvDaily).values(**row)
        stmt = stmt.on_duplicate_key_update(**{k: stmt.inserted[k] for k in row if k != "trade_date"})
        db.execute(stmt)
    return {**row, "trade_date": end.isoformat()}


def latest_market_env() -> dict | None:
    with session_scope() as db:
        row = db.execute(select(MarketEnvDaily).order_by(MarketEnvDaily.trade_date.desc()).limit(1)).scalars().first()
        if row is None:
            return None
        return env_to_dict(row)


def env_to_dict(row: MarketEnvDaily) -> dict:
    return {
        "trade_date": row.trade_date.isoformat(),
        "score": row.score,
        "regime": row.regime,
        "regime_name": REGIME_NAMES.get(row.regime, row.regime),
        "position_cap": row.position_cap,
        "indices": (row.index_state or {}).get("items", []),
        "breadth": row.breadth or {},
        "sectors": row.sectors or [],
        "detail": row.detail or {},
    }


def sector_strength_map() -> dict[str, float]:
    env = latest_market_env()
    if not env:
        return {}
    return {s["industry"]: s.get("strength", 50.0) for s in env["sectors"]}
