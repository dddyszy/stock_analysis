import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete
from sqlalchemy.dialects.mysql import insert

from app.chan import ChanConfig, ChanResult, MultiLevelView, Signal, analyze, combine
from app.core.config import get_settings
from app.db.models import ChanSignal, ChanSnapshot, KlineDaily, KlineWeekly
from app.db.session import session_scope
from app.services.strategy_config import get_active_params
from app.services.sync import load_bars

settings = get_settings()


@dataclass
class StockAnalysis:
    code: str
    day: ChanResult
    week: ChanResult | None
    view: MultiLevelView


def chan_config(params: dict | None = None) -> ChanConfig:
    params = params or get_active_params()
    return ChanConfig(b2_stop_mode=params.get("b2_stop_mode", "loose"), signal_recent_bars=params.get("signal_recent_bars", 10))


def analyze_stock(code: str, end: date | None = None, cfg: ChanConfig | None = None) -> StockAnalysis | None:
    daily = load_bars(KlineDaily, code, settings.analysis_daily_bars, end=end)
    if len(daily) < 60:
        return None
    weekly = load_bars(KlineWeekly, code, settings.analysis_weekly_bars, end=end)
    cfg = cfg or chan_config()
    day = analyze(daily, "day", cfg)
    week = analyze(weekly, "week", cfg) if len(weekly) >= 30 else None
    return StockAnalysis(code, day, week, combine(day, week))


def save_snapshot(a: StockAnalysis, calc_date: date) -> None:
    rows = [{"code": a.code, "level": "day", "calc_date": calc_date, "data": json.dumps(a.day.to_dict(include_bars=False), ensure_ascii=False)}]
    if a.week is not None:
        rows.append({"code": a.code, "level": "week", "calc_date": calc_date, "data": json.dumps(a.week.to_dict(include_bars=False), ensure_ascii=False)})
    with session_scope() as db:
        db.execute(delete(ChanSnapshot).where(ChanSnapshot.code == a.code, ChanSnapshot.calc_date < calc_date))
        stmt = insert(ChanSnapshot).values(rows)
        db.execute(stmt.on_duplicate_key_update(data=stmt.inserted.data))


def save_signals(code: str, signals: list[Signal], calc_date: date) -> None:
    if not signals:
        return
    rows = [
        {
            "code": code,
            # 线段级别与笔级别可能在同一天出现同类信号，level 里区分：day / day:seg
            "level": s.level if s.scope == "bi" else f"{s.level}:{s.scope}",
            "signal_type": s.type,
            "signal_date": s.dt,
            "price": s.price,
            "stop_price": s.stop_price,
            "target1": s.target1,
            "target2": s.target2,
            "strength": s.strength,
            "confirmed": s.confirmed,
            "calc_date": calc_date,
            "extra": {"desc": s.desc, **{k: v for k, v in s.extra.items() if k != "divergence"}},
        }
        for s in signals
    ]
    with session_scope() as db:
        stmt = insert(ChanSignal).values(rows)
        stmt = stmt.on_duplicate_key_update(
            price=stmt.inserted.price,
            stop_price=stmt.inserted.stop_price,
            target1=stmt.inserted.target1,
            target2=stmt.inserted.target2,
            strength=stmt.inserted.strength,
            confirmed=stmt.inserted.confirmed,
            calc_date=stmt.inserted.calc_date,
            extra=stmt.inserted.extra,
        )
        db.execute(stmt)
