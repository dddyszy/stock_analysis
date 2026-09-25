"""每日快照：股票状态、风险标签。回测按日期读取当时的数据，避免幸存者偏差和未来函数。

估值与评分本来就按日期存在 valuation_snapshot；这里补上 stock_basic、stock_risk_label 这类只保留最新值的表。
"""

from datetime import date, timedelta

from sqlalchemy import delete, func, insert, select

from app.db.models import (
    KlineDaily,
    StockBasic,
    StockRiskLabel,
    StockRiskLabelDaily,
    StockStatusDaily,
    ValuationSnapshot,
)
from app.db.session import session_scope

SUSPENDED_LABEL = "event:suspension_now"
FALLBACK_DAYS = 10  # 当天没有快照时，最多回退这么多天
VALUE_COLS = ("pe_ttm", "pb", "total_mv", "float_mv", "comp_score", "funm_score", "risk_score")


def latest_trade_date() -> date | None:
    with session_scope() as db:
        return db.scalar(select(func.max(KlineDaily.trade_date)))


def _replace(model, date_col, d: date, rows: list[dict]) -> None:
    with session_scope() as db:
        db.execute(delete(model).where(date_col == d))
        for i in range(0, len(rows), 1000):
            db.execute(insert(model).values(rows[i : i + 1000]))


def save_status_snapshot(trade_date: date | None = None) -> int:
    """当天在市股票的状态（名称、行业、ST、停牌）。同一天重复保存会覆盖。"""
    d = trade_date or latest_trade_date() or date.today()
    with session_scope() as db:
        suspended = set(db.execute(select(StockRiskLabel.code).where(StockRiskLabel.label == SUSPENDED_LABEL)).scalars())
        stocks = db.execute(select(StockBasic).where(StockBasic.is_index.is_(False), StockBasic.active.is_(True))).scalars().all()
        rows = [
            {"code": s.code, "trade_date": d, "name": s.name, "exchange": s.exchange, "industry": s.industry,
             "is_st": bool(s.is_st), "suspended": s.code in suspended}
            for s in stocks
        ]
    _replace(StockStatusDaily, StockStatusDaily.trade_date, d, rows)
    return len(rows)


def save_label_snapshot(snap_date: date | None = None) -> int:
    """把当前的风险标签整体复制一份到当天的快照。"""
    d = snap_date or date.today()
    with session_scope() as db:
        rows = [
            {"code": r.code, "label": r.label, "snap_date": d, "name": r.name, "severity": r.severity}
            for r in db.execute(select(StockRiskLabel)).scalars()
        ]
    _replace(StockRiskLabelDaily, StockRiskLabelDaily.snap_date, d, rows)
    return len(rows)


def _nearest(db, col, d: date) -> date | None:
    return db.scalar(select(func.max(col)).where(col <= d, col >= d - timedelta(days=FALLBACK_DAYS)))


def snapshot_at(d: date, codes: list[str] | None = None) -> dict:
    """某一天可见的股票状态、风险标签、估值与评分。

    当天没有快照时取之前最近的一天（最多回退 FALLBACK_DAYS 天），实际使用的日期放在 status_date / label_date 里；
    估值与评分逐只取 d 之前最近的非空值。"""
    with session_scope() as db:
        status_date = _nearest(db, StockStatusDaily.trade_date, d)
        label_date = _nearest(db, StockRiskLabelDaily.snap_date, d)
        stocks: dict[str, dict] = {}
        if status_date is not None:
            q = select(StockStatusDaily).where(StockStatusDaily.trade_date == status_date)
            if codes:
                q = q.where(StockStatusDaily.code.in_(codes))
            for s in db.execute(q).scalars():
                stocks[s.code] = {"name": s.name, "exchange": s.exchange, "industry": s.industry, "is_st": s.is_st,
                                  "suspended": s.suspended, "labels": []}
        if label_date is not None and stocks:
            q = select(StockRiskLabelDaily).where(StockRiskLabelDaily.snap_date == label_date)
            for r in db.execute(q).scalars():
                if r.code in stocks:
                    stocks[r.code]["labels"].append({"label": r.label, "name": r.name, "severity": r.severity})
        if stocks:
            q = select(ValuationSnapshot).where(
                ValuationSnapshot.trade_date <= d, ValuationSnapshot.trade_date >= d - timedelta(days=FALLBACK_DAYS)
            )
            if codes:
                q = q.where(ValuationSnapshot.code.in_(codes))
            for v in db.execute(q.order_by(ValuationSnapshot.trade_date)).scalars():
                s = stocks.get(v.code)
                if s is None:
                    continue
                for k in VALUE_COLS:
                    val = getattr(v, k)
                    if val is not None:
                        s[k] = val
    return {"date": d, "status_date": status_date, "label_date": label_date, "stocks": stocks}


def snapshot_health(d: date) -> dict:
    """某一天各类快照的行数，用于检测快照是否缺失。"""
    with session_scope() as db:
        count = lambda model, col: db.scalar(select(func.count()).select_from(model).where(col == d)) or 0  # noqa: E731
        return {
            "status": count(StockStatusDaily, StockStatusDaily.trade_date),
            "labels": count(StockRiskLabelDaily, StockRiskLabelDaily.snap_date),
            "valuation": count(ValuationSnapshot, ValuationSnapshot.trade_date),
            "scores": db.scalar(
                select(func.count()).select_from(ValuationSnapshot)
                .where(ValuationSnapshot.trade_date == d, ValuationSnapshot.comp_score.is_not(None))
            ) or 0,
        }
