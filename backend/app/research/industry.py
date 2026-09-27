"""申万一级行业等权指数：用本地日线的逐日等权平均收益累乘得到。"""

from datetime import date

import pandas as pd
from sqlalchemy import text

from app.db.session import engine

DAILY_CLIP = 0.25  # 单日收益截断，避免复权跳变把行业指数带偏


def industry_indices(start: date) -> dict[str, dict[date, float]]:
    sql = text(
        "SELECT k.code, k.trade_date, k.close, s.industry FROM kline_daily k JOIN stock_basic s ON s.code = k.code "
        "WHERE s.is_index = 0 AND s.industry IS NOT NULL AND k.trade_date >= :start"
    )
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"start": start})
    if df.empty:
        return {}
    df = df.sort_values(["code", "trade_date"])
    df["ret"] = df.groupby("code")["close"].pct_change().clip(-DAILY_CLIP, DAILY_CLIP)
    daily = df.dropna(subset=["ret"]).groupby(["industry", "trade_date"])["ret"].mean()
    out: dict[str, dict[date, float]] = {}
    for ind, series in daily.groupby(level=0):
        series = series.droplevel(0).sort_index()
        level = (1 + series).cumprod()
        out[str(ind)] = {(d.date() if hasattr(d, "date") and not isinstance(d, date) else d): float(v) for d, v in level.items()}
    return out
