"""每日结构状态：扫描时写入，供结构筛选器按状态筛选（第二次交付的结构异动也基于这张表）。"""

from datetime import date

from sqlalchemy import delete, func, insert, select

from app.analysis.structure import STATE_NAMES, STATE_ORDER, StructureState
from app.db.models import StockBasic, StockRiskLabel, StructureStateDaily
from app.db.session import session_scope


def state_row(code: str, d: date, st: StructureState, weekly: StructureState | None) -> dict:
    return {"code": code, "trade_date": d, "state": st.key, "price": st.price, "upper": st.upper, "lower": st.lower,
            "levels": st.levels, "signal": st.signal, "weekly_state": weekly.key if weekly else None}


def save_states(d: date, rows: list[dict]) -> int:
    with session_scope() as db:
        db.execute(delete(StructureStateDaily).where(StructureStateDaily.trade_date == d))
        for i in range(0, len(rows), 1000):
            db.execute(insert(StructureStateDaily).values(rows[i : i + 1000]))
    return len(rows)


def latest_date() -> date | None:
    with session_scope() as db:
        return db.scalar(select(func.max(StructureStateDaily.trade_date)))


def screen(states: list[str] | None = None, industry: str | None = None, exclude_risk: bool = False,
           limit: int = 500) -> dict:
    """按结构状态筛选最近一个交易日的股票，不打分、不排名，按信号日期、代码排序。"""
    d = latest_date()
    if d is None:
        return {"date": None, "counts": {}, "items": []}
    with session_scope() as db:
        counts = dict(db.execute(select(StructureStateDaily.state, func.count())
                                 .where(StructureStateDaily.trade_date == d).group_by(StructureStateDaily.state)).all())
        q = (select(StructureStateDaily, StockBasic.name, StockBasic.industry)
             .join(StockBasic, StockBasic.code == StructureStateDaily.code)
             .where(StructureStateDaily.trade_date == d))
        if states:
            q = q.where(StructureStateDaily.state.in_(states))
        if industry:
            q = q.where(StockBasic.industry == industry)
        rows = db.execute(q).all()
        labels: dict[str, list[str]] = {}
        for code, name in db.execute(select(StockRiskLabel.code, StockRiskLabel.name)
                                     .where(StockRiskLabel.code.in_([r[0].code for r in rows]))).all():
            labels.setdefault(code, []).append(name)
    items = []
    for s, name, ind in rows:
        risks = labels.get(s.code, [])
        if exclude_risk and risks:
            continue
        items.append({"code": s.code, "name": name, "industry": ind, "state": s.state, "state_name": STATE_NAMES.get(s.state, s.state),
                      "price": s.price, "upper": s.upper, "lower": s.lower, "signal": s.signal,
                      "weekly_state": s.weekly_state, "weekly_state_name": STATE_NAMES.get(s.weekly_state or "", None),
                      "risk_labels": risks})
    items.sort(key=lambda x: ((x["signal"] or {}).get("date") or "", x["code"]), reverse=True)
    return {"date": d.isoformat(), "total": len(items), "items": items[:limit],
            "counts": {k: counts.get(k, 0) for k in STATE_ORDER if counts.get(k)},
            "state_names": STATE_NAMES}
