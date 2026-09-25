from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now_col():
    return mapped_column(DateTime, server_default=func.now(), nullable=False)


def _updated_col():
    return mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


# ---------- MCP ----------


class McpCredential(Base):
    __tablename__ = "mcp_credential"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str | None] = mapped_column(String(255))
    client_info: Mapped[dict | None] = mapped_column(JSON)
    access_token: Mapped[str | None] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    token_type: Mapped[str | None] = mapped_column(String(32))
    scope: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    source: Mapped[str | None] = mapped_column(String(16))  # oauth / manual
    updated_at: Mapped[datetime] = _updated_col()


class McpCallLog(Base):
    __tablename__ = "mcp_call_log"
    __table_args__ = (Index("ix_mcp_call_log_created", "created_at"), Index("ix_mcp_call_log_tool", "tool"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tool: Mapped[str] = mapped_column(String(64))
    args: Mapped[str | None] = mapped_column(String(500))
    ok: Mapped[bool] = mapped_column(Boolean)
    duration_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = _now_col()


class SyncProgress(Base):
    __tablename__ = "sync_progress"

    task: Mapped[str] = mapped_column(String(32), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(String(1000))
    updated_at: Mapped[datetime] = _updated_col()


# ---------- 行情与基本面 ----------


class StockBasic(Base):
    __tablename__ = "stock_basic"
    __table_args__ = (Index("ix_stock_basic_industry", "industry"),)

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    exchange: Mapped[str] = mapped_column(String(4))
    industry: Mapped[str | None] = mapped_column(String(64))
    industry_code: Mapped[str | None] = mapped_column(String(32))
    list_date: Mapped[date | None] = mapped_column(Date)
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    is_index: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen: Mapped[date | None] = mapped_column(Date)
    last_seen: Mapped[date | None] = mapped_column(Date)
    missing_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 完整刷新中连续缺席的次数
    delisted_on: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = _updated_col()


class _KlineMixin:
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Double)
    high: Mapped[float] = mapped_column(Double)
    low: Mapped[float] = mapped_column(Double)
    close: Mapped[float] = mapped_column(Double)
    volume: Mapped[float | None] = mapped_column(Double)
    amount: Mapped[float | None] = mapped_column(Double)


class KlineDaily(_KlineMixin, Base):
    __tablename__ = "kline_daily"
    __table_args__ = (Index("ix_kline_daily_date", "trade_date"),)


class KlineWeekly(_KlineMixin, Base):
    __tablename__ = "kline_weekly"
    __table_args__ = (Index("ix_kline_weekly_date", "trade_date"),)


class FundamentalQuarterly(Base):
    __tablename__ = "fundamental_quarterly"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    revenue: Mapped[float | None] = mapped_column(Double)
    net_profit: Mapped[float | None] = mapped_column(Double)
    revenue_yoy: Mapped[float | None] = mapped_column(Double)
    profit_yoy: Mapped[float | None] = mapped_column(Double)
    gross_margin: Mapped[float | None] = mapped_column(Double)
    roe: Mapped[float | None] = mapped_column(Double)
    debt_ratio: Mapped[float | None] = mapped_column(Double)
    op_cashflow: Mapped[float | None] = mapped_column(Double)
    goodwill: Mapped[float | None] = mapped_column(Double)
    total_equity: Mapped[float | None] = mapped_column(Double)
    total_assets: Mapped[float | None] = mapped_column(Double)
    raw: Mapped[dict | None] = mapped_column(JSON)
    first_seen: Mapped[date | None] = mapped_column(Date)  # 估计的可获得日期：首次拉到与法定披露截止日取早者
    updated_at: Mapped[datetime] = _updated_col()


class ValuationSnapshot(Base):
    __tablename__ = "valuation_snapshot"
    __table_args__ = (Index("ix_valuation_snapshot_date", "trade_date"),)

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    pe_ttm: Mapped[float | None] = mapped_column(Double)
    pb: Mapped[float | None] = mapped_column(Double)
    roe_ttm: Mapped[float | None] = mapped_column(Double)
    dividend_yield: Mapped[float | None] = mapped_column(Double)
    total_mv: Mapped[float | None] = mapped_column(Double)
    float_mv: Mapped[float | None] = mapped_column(Double)
    comp_score: Mapped[float | None] = mapped_column(Double)
    funm_score: Mapped[float | None] = mapped_column(Double)
    risk_score: Mapped[float | None] = mapped_column(Double)
    raw: Mapped[dict | None] = mapped_column(JSON)


class StockRiskLabel(Base):
    """腾讯标签选股（tool_label）批量拉取的风险标签，每周刷新。"""

    __tablename__ = "stock_risk_label"
    __table_args__ = (Index("ix_stock_risk_label_label", "label"),)

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    label: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(8))  # hard：硬过滤；penalty：扣分
    snap_date: Mapped[date] = mapped_column(Date)
    updated_at: Mapped[datetime] = _updated_col()


class StockStatusDaily(Base):
    """每个交易日的股票状态快照，供回测按当时的股票池、ST 和停牌状态抽样。"""

    __tablename__ = "stock_status_daily"
    __table_args__ = (Index("ix_stock_status_daily_date", "trade_date"),)

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    exchange: Mapped[str] = mapped_column(String(4))
    industry: Mapped[str | None] = mapped_column(String(64))
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)


class StockRiskLabelDaily(Base):
    """风险标签的每日快照（stock_risk_label 只保留最新一份）。"""

    __tablename__ = "stock_risk_label_daily"
    __table_args__ = (Index("ix_stock_risk_label_daily_date", "snap_date"),)

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    label: Mapped[str] = mapped_column(String(64), primary_key=True)
    snap_date: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(8))


class MarketEnvDaily(Base):
    __tablename__ = "market_env_daily"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    score: Mapped[float] = mapped_column(Double)
    regime: Mapped[str] = mapped_column(String(16))
    position_cap: Mapped[float] = mapped_column(Double)
    index_state: Mapped[dict | None] = mapped_column(JSON)
    breadth: Mapped[dict | None] = mapped_column(JSON)
    sectors: Mapped[list | None] = mapped_column(JSON)
    detail: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = _updated_col()


# ---------- 缠论结果 ----------


class ChanSnapshot(Base):
    __tablename__ = "chan_snapshot"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    level: Mapped[str] = mapped_column(String(8), primary_key=True)
    calc_date: Mapped[date] = mapped_column(Date, primary_key=True)
    data: Mapped[str] = mapped_column(MEDIUMTEXT)
    updated_at: Mapped[datetime] = _updated_col()


class ChanSignal(Base):
    __tablename__ = "chan_signal"
    __table_args__ = (
        UniqueConstraint("code", "level", "signal_type", "signal_date", name="uq_chan_signal"),
        Index("ix_chan_signal_date", "signal_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16))
    level: Mapped[str] = mapped_column(String(8))
    signal_type: Mapped[str] = mapped_column(String(8))
    signal_date: Mapped[date] = mapped_column(Date)
    price: Mapped[float] = mapped_column(Double)
    stop_price: Mapped[float | None] = mapped_column(Double)
    target1: Mapped[float | None] = mapped_column(Double)
    target2: Mapped[float | None] = mapped_column(Double)
    strength: Mapped[float | None] = mapped_column(Double)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    calc_date: Mapped[date] = mapped_column(Date)
    extra: Mapped[dict | None] = mapped_column(JSON)


# ---------- 推荐 ----------


class RecommendRun(Base):
    __tablename__ = "recommend_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    market_regime: Mapped[str | None] = mapped_column(String(16))
    position_cap: Mapped[float | None] = mapped_column(Double)
    total_scanned: Mapped[int] = mapped_column(Integer, default=0)
    total_selected: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[dict | None] = mapped_column(JSON)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()


class RecommendItem(Base):
    __tablename__ = "recommend_item"
    __table_args__ = (Index("ix_recommend_item_run", "run_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("recommend_run.id", ondelete="CASCADE"))
    rank: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(String(64))
    industry: Mapped[str | None] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Double)
    chan_score: Mapped[float] = mapped_column(Double)
    fund_score: Mapped[float] = mapped_column(Double)
    sector_score: Mapped[float] = mapped_column(Double)
    signal_type: Mapped[str] = mapped_column(String(8))
    signal_date: Mapped[date] = mapped_column(Date)
    price: Mapped[float] = mapped_column(Double)
    stop_price: Mapped[float | None] = mapped_column(Double)
    target1: Mapped[float | None] = mapped_column(Double)
    target2: Mapped[float | None] = mapped_column(Double)
    reasons: Mapped[list | None] = mapped_column(JSON)
    pool: Mapped[str] = mapped_column(String(8), default="main", server_default="main")  # main / watch
    scope: Mapped[str] = mapped_column(String(8), default="bi", server_default="bi")  # bi / seg
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    strong_div: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    avg_amount: Mapped[float | None] = mapped_column(Double)
    risk_labels: Mapped[list | None] = mapped_column(JSON)


class RecommendPerf(Base):
    """推荐跟踪：推荐之后 5/10/20 个交易日的表现，入场价取推荐日次日开盘价。"""

    __tablename__ = "recommend_perf"
    __table_args__ = (Index("ix_recommend_perf_run", "run_id"),)

    item_id: Mapped[int] = mapped_column(ForeignKey("recommend_item.id", ondelete="CASCADE"), primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer)
    run_date: Mapped[date] = mapped_column(Date)
    code: Mapped[str] = mapped_column(String(16))
    signal_type: Mapped[str] = mapped_column(String(8))
    scope: Mapped[str] = mapped_column(String(8))
    pool: Mapped[str] = mapped_column(String(8))
    regime: Mapped[str | None] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Double)
    entry_date: Mapped[date | None] = mapped_column(Date)
    entry_price: Mapped[float | None] = mapped_column(Double)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)  # 一字涨停买不进
    days: Mapped[int] = mapped_column(Integer, default=0)  # 已经过的交易日数
    ret5: Mapped[float | None] = mapped_column(Double)
    ret10: Mapped[float | None] = mapped_column(Double)
    ret20: Mapped[float | None] = mapped_column(Double)
    ex300_5: Mapped[float | None] = mapped_column(Double)
    ex300_10: Mapped[float | None] = mapped_column(Double)
    ex300_20: Mapped[float | None] = mapped_column(Double)
    ex1000_5: Mapped[float | None] = mapped_column(Double)
    ex1000_10: Mapped[float | None] = mapped_column(Double)
    ex1000_20: Mapped[float | None] = mapped_column(Double)
    max_drawdown: Mapped[float | None] = mapped_column(Double)
    first_hit: Mapped[str | None] = mapped_column(String(8))  # stop / target / None
    updated_at: Mapped[datetime] = _updated_col()


class Watchlist(Base):
    __tablename__ = "watchlist"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    note: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str | None] = mapped_column(String(32))
    added_at: Mapped[datetime] = _now_col()


# ---------- 策略与持仓 ----------


class StrategyConfig(Base):
    __tablename__ = "strategy_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    params: Mapped[dict] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _updated_col()


class PositionPlan(Base):
    __tablename__ = "position_plan"
    __table_args__ = (Index("ix_position_plan_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="Initial")
    entry_signal: Mapped[str | None] = mapped_column(String(8))
    entry_signal_date: Mapped[date | None] = mapped_column(Date)
    entry_date: Mapped[date] = mapped_column(Date)
    entry_price: Mapped[float] = mapped_column(Double)
    quantity: Mapped[int] = mapped_column(Integer)
    remaining_qty: Mapped[int] = mapped_column(Integer)
    r_value: Mapped[float] = mapped_column(Double)
    initial_stop: Mapped[float] = mapped_column(Double)
    current_stop: Mapped[float] = mapped_column(Double)
    stop_source: Mapped[str | None] = mapped_column(String(64))
    target1: Mapped[float | None] = mapped_column(Double)
    target2: Mapped[float | None] = mapped_column(Double)
    highest_price: Mapped[float | None] = mapped_column(Double)
    reduce_steps: Mapped[int] = mapped_column(Integer, default=0)
    config_id: Mapped[int | None] = mapped_column(Integer)
    linked_sim: Mapped[bool] = mapped_column(Boolean, default=True)
    last_eval_date: Mapped[date | None] = mapped_column(Date)
    last_advice: Mapped[dict | None] = mapped_column(JSON)
    realized_pnl: Mapped[float] = mapped_column(Double, default=0.0)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _updated_col()


class PositionEvent(Base):
    __tablename__ = "position_event"
    __table_args__ = (Index("ix_position_event_plan", "plan_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("position_plan.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(16))
    level: Mapped[str] = mapped_column(String(8), default="execute")  # warning / execute / info
    trade_date: Mapped[date] = mapped_column(Date)
    price: Mapped[float | None] = mapped_column(Double)
    quantity: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(255))
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _now_col()


# ---------- 模拟盘 ----------


class SimOrder(Base):
    __tablename__ = "sim_order"
    __table_args__ = (Index("ix_sim_order_code", "code"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(8))
    price: Mapped[float] = mapped_column(Double)
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    filled_price: Mapped[float | None] = mapped_column(Double)
    plan_id: Mapped[int | None] = mapped_column(Integer)
    signal_type: Mapped[str | None] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(16), default="manual")
    message: Mapped[str | None] = mapped_column(String(500))
    raw: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _updated_col()


class SimPositionSnapshot(Base):
    __tablename__ = "sim_position_snapshot"
    __table_args__ = (UniqueConstraint("snap_date", "code", name="uq_sim_pos_snap"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snap_date: Mapped[date] = mapped_column(Date)
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer)
    available: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[float | None] = mapped_column(Double)
    price: Mapped[float | None] = mapped_column(Double)
    market_value: Mapped[float | None] = mapped_column(Double)
    pnl: Mapped[float | None] = mapped_column(Double)
    pnl_pct: Mapped[float | None] = mapped_column(Double)
    raw: Mapped[dict | None] = mapped_column(JSON)


class SimAccountDaily(Base):
    __tablename__ = "sim_account_daily"

    snap_date: Mapped[date] = mapped_column(Date, primary_key=True)
    total_asset: Mapped[float | None] = mapped_column(Double)
    cash: Mapped[float | None] = mapped_column(Double)
    market_value: Mapped[float | None] = mapped_column(Double)
    total_pnl: Mapped[float | None] = mapped_column(Double)
    raw: Mapped[dict | None] = mapped_column(JSON)


# ---------- 写回 App ----------


class NotifyLog(Base):
    """系统内通知。dedupe_key 相同的通知只记一次（同一事件同一天）。"""

    __tablename__ = "notify_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(16))  # info / warning / error / important
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str | None] = mapped_column(String(2000))
    dedupe_key: Mapped[str] = mapped_column(String(191), unique=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = _now_col()


class RuntimeState(Base):
    """运行时状态的键值存储，例如 MCP 工具的配额冷却截止时间。"""

    __tablename__ = "runtime_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = _updated_col()


class AppSyncState(Base):
    __tablename__ = "app_sync_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = _updated_col()


class AppSyncLog(Base):
    __tablename__ = "app_sync_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(32))
    code: Mapped[str | None] = mapped_column(String(255))
    ok: Mapped[bool] = mapped_column(Boolean)
    message: Mapped[str | None] = mapped_column(String(1000))
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _now_col()


# ---------- 任务与回测 ----------


class JobLog(Base):
    __tablename__ = "job_log"
    __table_args__ = (Index("ix_job_log_name", "job_name", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="running")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = _now_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class BacktestRun(Base):
    __tablename__ = "backtest_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    params: Mapped[dict | None] = mapped_column(JSON)
    summary: Mapped[dict | None] = mapped_column(JSON)
    by_signal: Mapped[dict | None] = mapped_column(JSON)
    suggested_weights: Mapped[dict | None] = mapped_column(JSON)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class BacktestTrade(Base):
    __tablename__ = "backtest_trade"
    __table_args__ = (Index("ix_backtest_trade_run", "run_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_run.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(16))
    signal_type: Mapped[str] = mapped_column(String(8))
    entry_date: Mapped[date] = mapped_column(Date)
    entry_price: Mapped[float] = mapped_column(Double)
    exit_date: Mapped[date | None] = mapped_column(Date)
    exit_price: Mapped[float | None] = mapped_column(Double)
    r_multiple: Mapped[float | None] = mapped_column(Double)
    pnl_pct: Mapped[float | None] = mapped_column(Double)
    exit_reason: Mapped[str | None] = mapped_column(String(64))
    holding_days: Mapped[int | None] = mapped_column(Integer)
    scope: Mapped[str | None] = mapped_column(String(8))
    bench_ret: Mapped[float | None] = mapped_column(Double)  # 同期中证 1000 收益（%）
    excess: Mapped[float | None] = mapped_column(Double)  # 相对中证 1000 的超额（%）
    segment: Mapped[str | None] = mapped_column(String(8))  # in / out（样本内 / 样本外）
    is_control: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    tags: Mapped[dict | None] = mapped_column(JSON)  # 入场时的市场状态 / 周线共振 / 背驰强度
