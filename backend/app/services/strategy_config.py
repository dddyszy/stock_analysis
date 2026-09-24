"""策略参数：止盈止损、仓位、打分权重。数据库里可以存多套，激活的一套生效。"""

import copy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import StrategyConfig
from app.db.session import session_scope

DEFAULT_PARAMS: dict = {
    # 开仓风控
    "default_equity": 1_000_000,  # 拿不到模拟账户资产时用于计算仓位
    "risk_per_trade": 0.01,
    "max_single_position": 0.20,
    "min_reward_risk": 2.0,
    "max_stop_distance": 0.12,
    "wide_stop_action": "half",  # half：仓位减半；skip：不开仓
    # 止损
    "hard_stop_pct": 0.08,
    "stop_buffer_atr": 0.5,
    "stop_buffer_pct": 0.01,
    "time_stop_bars": 10,
    "time_stop_reduce": 0.5,
    "b2_stop_mode": "loose",
    # 止盈分批：前两次各卖原始仓位的比例，第三次卖出剩余
    "batch_ratios": [0.3333, 0.3333],
    "keep_base_ratio_week_uptrend": 0.3333,
    # 下单
    "limit_price_offset": 0.005,
    "fees": {"commission": 0.00025, "min_commission": 5.0, "stamp_tax": 0.0005},
    # 市场温度 → 总仓位上限
    "regime_caps": {"strong": 0.8, "neutral": 0.5, "weak": 0.2},
    # 推荐打分
    "weights": {"chan": 0.5, "fund": 0.3, "sector": 0.2},
    "signal_weights": {"B1": 0.8, "B2": 1.0, "B3": 0.9},
    "regime_multiplier": {"strong": 1.0, "neutral": 0.9, "weak": 0.75},
    "signal_recent_bars": 10,
    "recommend_top_n": 50,
    "min_fund_score": 40,
    # A 股流动性与风险
    "min_avg_amount": 1e8,  # 近 20 日平均成交额下限（元）
    "min_float_mv": 30e8,  # 流通市值下限（元）
    "risk_label_penalty": 8,  # 每个扣分类风险标签扣的基本面分
    # 信号质量
    "recommend_confirmed_only": True,  # 主推荐只收已确认信号，未确认的进观察池
    "scope_weight": 1.2,  # 线段级别信号相对笔级别的加权
    "resonance_weight": 0.0,  # 周线共振在缠论分中的权重；回测显示共振向上时入场反而更差，默认不加分
    "regime_block": ["B1|up", "B3|down"],  # 这些"信号|指数市场状态"组合回测显著跑输随机组，只进观察池
    "watch_top_n": 30,
    # 回测
    "slippage": 0.001,  # 单边滑点
    "backtest_split_ratio": 0.7,  # 样本内占回看区间的比例
    "finance_time_budget": 480,  # 每次推荐补拉候选股财报的最长时间（秒），受 MCP 限频影响
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_default(db: Session) -> StrategyConfig:
    active = db.execute(select(StrategyConfig).where(StrategyConfig.is_active.is_(True))).scalars().first()
    if active:
        return active
    cfg = db.execute(select(StrategyConfig).where(StrategyConfig.name == "default")).scalars().first()
    if cfg is None:
        cfg = StrategyConfig(name="default", params=copy.deepcopy(DEFAULT_PARAMS), is_active=True, note="系统默认参数")
        db.add(cfg)
    else:
        cfg.is_active = True
    db.flush()
    return cfg


def get_active_params() -> dict:
    with session_scope() as db:
        cfg = ensure_default(db)
        return _merge(DEFAULT_PARAMS, cfg.params)


def get_active_config() -> tuple[int, dict]:
    with session_scope() as db:
        cfg = ensure_default(db)
        return cfg.id, _merge(DEFAULT_PARAMS, cfg.params)


def save_config(name: str, params: dict, activate: bool = False, note: str | None = None) -> int:
    with session_scope() as db:
        cfg = db.execute(select(StrategyConfig).where(StrategyConfig.name == name)).scalars().first()
        merged = _merge(DEFAULT_PARAMS, params)
        if cfg is None:
            cfg = StrategyConfig(name=name, params=merged, note=note)
            db.add(cfg)
        else:
            cfg.params = merged
            if note is not None:
                cfg.note = note
        if activate:
            for other in db.execute(select(StrategyConfig)).scalars():
                other.is_active = False
            cfg.is_active = True
        db.flush()
        return cfg.id


def list_configs() -> list[dict]:
    with session_scope() as db:
        ensure_default(db)
        rows = db.execute(select(StrategyConfig).order_by(StrategyConfig.id)).scalars().all()
        return [
            {"id": r.id, "name": r.name, "params": _merge(DEFAULT_PARAMS, r.params), "is_active": r.is_active, "note": r.note}
            for r in rows
        ]


def activate_config(config_id: int) -> None:
    with session_scope() as db:
        for cfg in db.execute(select(StrategyConfig)).scalars():
            cfg.is_active = cfg.id == config_id
