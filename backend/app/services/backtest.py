"""历史回测：逐根 K 线向前推进、每步只用当时可见的数据重新计算缠论，避免"重画"带来的未来函数。

执行规则与持仓策略一致的简化版：
- 默认只在信号所在笔确认后入场（entry_mode=confirmed），次日开盘价买入；
- 结构止损按收盘确认、次日开盘卖出；硬止损盘中触发；T+1；
- 涨跌停按板块判断（ashare_rules），一字涨停买不进、一字跌停卖不出；买卖各计一次滑点；
- 浮盈 1R 保本，新的确认向上笔上移止损；到目标一 / 一卖 / 二卖分批，三卖或止损清仓；时间止损减半。

为了判断买点是否真的有优势：
- 随机入场对照组：同一批股票、同一时段随机入场，止损距离和盈亏比取真实交易的中位数，其余规则相同；
- 指数基准：每笔交易计算同期中证 1000 的收益和超额；
- 样本内外：按 split_date 切分，建议权重只用样本内数据计算。

诊断：每笔交易记录入场时的市场状态（中证 1000 相对 60 日均线）、周线共振和背驰强度，
按维度与同市场状态下的随机组比较；逐年滚动检验只用此前年份挑选"信号 × 市场状态"组合，再在下一年验证。

已知局限：股票池是当前在市的股票，已退市股票的历史拿不到，存在幸存者偏差（会高估一买类信号）。
基本面和风险标签只有当前快照，不参与回测。
"""

import asyncio
import bisect
import logging
import os
import random
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
from sqlalchemy import select

from app.analysis.market_env import INDEX_REGIME_CODE, INDEX_REGIME_NAMES, index_regime_map
from app.chan import TYPE_NAMES, analyze, combine
from app.chan.types import UP
from app.db.models import BacktestRun, BacktestTrade, KlineDaily, StockBasic
from app.db.session import session_scope
from app.services.ashare_rules import is_one_price_limit_down, is_one_price_limit_up
from app.services.chan_service import chan_config
from app.services.jobs import JobContext
from app.services.kline_utils import resample_weekly
from app.services.strategy_config import get_active_params, save_config
from app.services.sync import load_bars

logger = logging.getLogger(__name__)

WINDOW = 400
BENCH_CODE = INDEX_REGIME_CODE  # 中证 1000
SURVIVORSHIP_NOTE = "股票池为当前在市股票，已退市股票的历史无法获取，结果存在幸存者偏差，一买类信号可能被高估。"


@dataclass
class _Pos:
    signal_type: str
    scope: str
    entry_idx: int
    entry_price: float
    stop: float
    r: float
    target1: float | None
    remaining: float = 1.0
    realized: float = 0.0  # 以价格计的累计收益（按份额加权）
    steps: int = 0
    time_stopped: bool = False
    target_hit: bool = False
    trail_reduced: bool = False
    handled_sells: set = field(default_factory=set)
    tags: dict = field(default_factory=dict)


@dataclass
class _ControlSpec:
    prob: float  # 每根空仓 K 线随机入场的概率
    stop_pct: float
    rr: float


def _fee(params: dict, price: float, sell: bool) -> float:
    f = params["fees"]
    cost = price * f["commission"]
    if sell:
        cost += price * f["stamp_tax"]
    return cost


def backtest_series(code: str, bars: list, params: dict, warmup: int = 250, entry_mode: str = "confirmed",
                    use_rr_filter: bool = True, is_st: bool = False, control: _ControlSpec | None = None,
                    rng: random.Random | None = None) -> list[dict]:
    cfg = chan_config(params)
    slip = float(params.get("slippage", 0.0))
    trades: list[dict] = []
    seen: set = set()
    pos: _Pos | None = None
    n = len(bars)
    pending_exit: tuple[str, float] | None = None  # (原因, 卖出比例)
    rng = rng or random.Random(0)

    def close_part(idx: int, price: float, ratio: float, reason: str) -> None:
        nonlocal pos
        ratio = min(ratio, pos.remaining)
        sell_px = price * (1 - slip)
        pos.realized += (sell_px - _fee(params, sell_px, True) - pos.entry_price - _fee(params, pos.entry_price, False)) * ratio
        pos.remaining -= ratio
        pos.steps += 1
        if pos.remaining <= 1e-6:
            trades.append({
                "code": code, "signal_type": pos.signal_type, "scope": pos.scope,
                "entry_date": bars[pos.entry_idx].dt, "entry_price": pos.entry_price,
                "exit_date": bars[idx].dt, "exit_price": sell_px, "r_multiple": pos.realized / pos.r if pos.r else 0.0,
                "pnl_pct": pos.realized / pos.entry_price * 100, "exit_reason": reason, "holding_days": idx - pos.entry_idx,
                "stop_pct": pos.r / pos.entry_price,
                "rr": (pos.target1 - pos.entry_price) / pos.r if pos.target1 and pos.r else None,
                "is_control": control is not None,
                "tags": {**pos.tags, "target_hit": pos.target_hit},
            })
            pos = None

    for t in range(warmup, n - 1):
        start = max(0, t - WINDOW)
        window = bars[start : t + 1]
        off = start
        bar = bars[t]
        nxt = bars[t + 1]

        if pos is not None:
            # 前一日收盘决定的卖出在今天开盘执行（一字跌停顺延）
            if pending_exit is not None and t > pos.entry_idx:
                reason, ratio = pending_exit
                if not is_one_price_limit_down(bar, bars[t - 1].close, code, is_st):
                    if reason == "close":
                        close_part(t, bar.open, pos.remaining, "结构止损")
                    else:
                        close_part(t, bar.open, ratio, reason)
                    pending_exit = None
                if pos is None:
                    continue
            res = analyze(window, "day", cfg)
            held = t - pos.entry_idx
            if held >= 1:
                hard = pos.entry_price * (1 - params["hard_stop_pct"])
                if bar.low <= hard and not is_one_price_limit_down(bar, bars[t - 1].close, code, is_st):
                    close_part(t, min(bar.open, hard), pos.remaining, "硬止损")
                    continue
                if bar.close < pos.stop:
                    trailing = pos.stop > pos.entry_price + 1e-9
                    if trailing and not pos.trail_reduced:
                        pos.trail_reduced = True
                        pending_exit = ("跟踪止损", params["batch_ratios"][1])
                    else:
                        pending_exit = ("close", pos.remaining)
                    continue
                buf = min(max((bar.high - bar.low) * params["stop_buffer_atr"], bar.close * 0.005), bar.close * 0.03)
                if bar.close >= pos.entry_price + pos.r:
                    pos.stop = max(pos.stop, pos.entry_price)
                ups = [b for b in res.bis if b.direction == UP and b.confirmed and b.start_raw + off >= pos.entry_idx]
                if ups:
                    pos.stop = max(pos.stop, ups[-1].start.price - buf)
                if pos.target1 and bar.high >= pos.target1 and not pos.target_hit:
                    pos.target_hit = True
                    close_part(t, max(pos.target1, bar.open), params["batch_ratios"][0], "目标一")
                    if pos is None:
                        continue
                for s in res.signals:
                    if s.is_buy or not s.confirmed or s.raw_idx + off < pos.entry_idx:
                        continue
                    key = (s.type, s.scope, s.dt)
                    if key in pos.handled_sells:
                        continue
                    pos.handled_sells.add(key)
                    if s.type == "S1":
                        pending_exit = ("一卖", params["batch_ratios"][0])
                    elif s.type == "S2":
                        pending_exit = ("二卖", params["batch_ratios"][1])
                    elif s.type == "S3":
                        pending_exit = ("close", pos.remaining)
                if pending_exit is None and not pos.time_stopped and not ups and held >= params["time_stop_bars"] and pos.steps == 0:
                    pos.time_stopped = True
                    pending_exit = ("时间止损", params["time_stop_reduce"])
            continue

        # 空仓：次日开盘买入，一字涨停买不进
        if is_one_price_limit_up(nxt, bar.close, code, is_st):
            continue
        entry = nxt.open * (1 + slip)
        if control is not None:
            if rng.random() >= control.prob:
                continue
            stop = entry * (1 - control.stop_pct)
            r = entry - stop
            pos = _Pos("RND", "rnd", t + 1, entry, stop, r, entry + control.rr * r)
            pending_exit = None
            continue

        res = analyze(window, "day", cfg)
        for s in res.signals:
            if not s.is_buy or s.extra.get("invalidated"):
                continue
            if entry_mode == "confirmed" and not s.confirmed:
                continue
            key = (s.type, s.scope, s.dt)
            if key in seen:
                continue
            seen.add(key)
            if (t - (s.raw_idx + off)) > params["signal_recent_bars"]:
                continue
            atr_proxy = sum(b.high - b.low for b in window[-14:]) / min(14, len(window))
            buf = min(max(atr_proxy * params["stop_buffer_atr"], entry * 0.005), entry * 0.03)
            stop = (s.stop_price if s.stop_price is not None else s.price) - buf
            r = entry - stop
            if r <= 0:
                continue
            rr = (s.target1 - entry) / r if s.target1 else 0
            if use_rr_filter and rr < params["min_reward_risk"]:
                continue
            if r / entry > params["max_stop_distance"] and params["wide_stop_action"] == "skip":
                continue
            target1 = s.target1
            cap = params.get("target_cap_r")
            if cap and target1:
                target1 = min(target1, entry + cap * r)
            pos = _Pos(s.type, s.scope, t + 1, entry, stop, r, target1, tags=_entry_tags(bars, t, res, s, cfg))
            pending_exit = None
            break

    if pos is not None:
        last = bars[-1]
        close_part(n - 1, last.close, pos.remaining, "回测结束平仓")
    return trades


def _entry_tags(bars: list, t: int, res, s, cfg) -> dict:
    """入场时可见的周线共振与背驰强度（周线由截至当天的日线合成，最后一周可能不完整）。"""
    weekly = resample_weekly(bars[: t + 1])
    wres = analyze(weekly, "week", cfg) if len(weekly) >= 30 else None
    reso = combine(res, wres).resonance
    div = s.extra.get("divergence")
    return {
        "week": "up" if reso >= 0.2 else "down" if reso <= -0.2 else "flat",
        "div": ("strong" if div.get("strong") else "normal") if div else "none",
    }


# ---------- 统计 ----------


def _stat(ts: list[dict]) -> dict:
    if not ts:
        return {"trades": 0}
    rs = [t["r_multiple"] for t in ts]
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r < 0]
    ex = [t["excess"] for t in ts if t.get("excess") is not None]
    return {
        "trades": len(ts),
        "win_rate": round(len(wins) / len(ts), 3),
        "avg_r": round(sum(rs) / len(rs), 3),
        "avg_pnl_pct": round(sum(t["pnl_pct"] for t in ts) / len(ts), 2),
        "avg_excess": round(sum(ex) / len(ex), 2) if ex else None,
        "profit_factor": round(sum(wins) / sum(losses), 2) if losses else None,
        "avg_holding_days": round(sum(t["holding_days"] for t in ts) / len(ts), 1),
        "max_loss_r": round(min(rs), 2),
        "target_hit_ratio": round(sum(1 for t in ts if (t.get("tags") or {}).get("target_hit")) / len(ts), 3),
        "max_win_r": round(max(rs), 2),
    }


def bootstrap_edge(a: list[float], b: list[float], n: int = 1000, seed: int = 7) -> dict | None:
    """两组 R 倍数均值之差的自助法 95% 置信区间。"""
    if len(a) < 5 or len(b) < 5:
        return None
    rng = random.Random(seed)
    diffs = []
    for _ in range(n):
        sa = [a[rng.randrange(len(a))] for _ in a]
        sb = [b[rng.randrange(len(b))] for _ in b]
        diffs.append(sum(sa) / len(sa) - sum(sb) / len(sb))
    diffs.sort()
    edge = sum(a) / len(a) - sum(b) / len(b)
    lo, hi = diffs[int(n * 0.025)], diffs[int(n * 0.975) - 1]
    return {"edge": round(edge, 3), "ci_low": round(lo, 3), "ci_high": round(hi, 3), "significant": lo > 0 or hi < 0}


REGIME_NAMES = INDEX_REGIME_NAMES
WEEK_NAMES = {"up": "周线共振向上", "flat": "周线中性", "down": "周线向下"}
DIV_NAMES = {"strong": "强背驰", "normal": "普通背驰", "none": "无背驰数据"}
SCOPE_NAMES = {"bi": "笔级别", "seg": "线段级别"}


def _regime(t: dict) -> str:
    return (t.get("tags") or {}).get("regime", "unknown")


def _year_regime(t: dict) -> str:
    return f"{t['entry_date'].year}|{_regime(t)}"


def matched_edge(sig: list[dict], control: list[dict], strata=_regime, n: int = 500, seed: int = 7) -> dict | None:
    """信号组与同分层随机组的平均 R 之差。随机组按信号组在各分层的占比加权，剔除择时带来的差异；
    置信区间用分层自助法估计。分层内随机组少于 5 笔的信号交易不参与比较。"""
    groups: dict[str, list[dict]] = {}
    for t in control:
        groups.setdefault(strata(t), []).append(t)
    covered = [t for t in sig if len(groups.get(strata(t), [])) >= 5]
    if len(covered) < 5:
        return None
    weights: dict[str, int] = {}
    for t in covered:
        weights[strata(t)] = weights.get(strata(t), 0) + 1
    rng = np.random.default_rng(seed)
    a = np.array([t["r_multiple"] for t in covered])
    sig_means = a[rng.integers(0, len(a), (n, len(a)))].mean(axis=1)
    ctrl_means = np.zeros(n)
    ctrl_r = ctrl_ex = 0.0
    for k, w in weights.items():
        share = w / len(covered)
        c = np.array([t["r_multiple"] for t in groups[k]])
        ctrl_r += share * float(c.mean())
        ex = [t["excess"] for t in groups[k] if t.get("excess") is not None]
        ctrl_ex += share * (sum(ex) / len(ex) if ex else 0.0)
        ctrl_means += share * c[rng.integers(0, len(c), (n, len(c)))].mean(axis=1)
    diffs = np.sort(sig_means - ctrl_means)
    lo, hi = float(diffs[int(n * 0.025)]), float(diffs[int(n * 0.975) - 1])
    sig_ex = [t["excess"] for t in covered if t.get("excess") is not None]
    return {
        "trades": len(covered),
        "edge": round(float(a.mean()) - ctrl_r, 3), "ci_low": round(lo, 3), "ci_high": round(hi, 3),
        "significant": lo > 0 or hi < 0,
        "control_avg_r": round(ctrl_r, 3),
        "excess_edge": round((sum(sig_ex) / len(sig_ex) if sig_ex else 0.0) - ctrl_ex, 2),
    }


def _unit(t: dict) -> str:
    return f"{t['signal_type']}|{_regime(t)}"


def _unit_name(u: str) -> str:
    sig, reg = u.split("|")
    return f"{TYPE_NAMES.get(sig, sig)} · {REGIME_NAMES.get(reg, reg)}"


def diagnose(trades: list[dict], control: list[dict]) -> list[dict]:
    """按维度拆分信号交易，每组与同市场状态的随机组比较。"""
    tag = lambda k, default: (lambda t: (t.get("tags") or {}).get(k, default))  # noqa: E731
    dims = [
        ("signal_type", "信号类型", lambda t: t["signal_type"], lambda v: TYPE_NAMES.get(v, v), _regime),
        ("regime", "市场状态", _regime, lambda v: REGIME_NAMES.get(v, v), _regime),
        ("type_regime", "信号 × 市场状态", _unit, _unit_name, _regime),
        ("year", "年份", lambda t: str(t["entry_date"].year), lambda v: f"{v} 年", _year_regime),
        ("week", "周线共振", tag("week", "flat"), lambda v: WEEK_NAMES.get(v, v), _regime),
        ("div", "背驰强度", tag("div", "none"), lambda v: DIV_NAMES.get(v, v), _regime),
        ("scope", "信号级别", lambda t: t.get("scope") or "bi", lambda v: SCOPE_NAMES.get(v, v), _regime),
    ]
    out = []
    for key, title, fn, name, strata in dims:
        buckets: dict[str, list[dict]] = {}
        for t in trades:
            buckets.setdefault(fn(t), []).append(t)
        rows = [
            {"key": v, "name": name(v), **_stat(ts), "matched": matched_edge(ts, control, strata)}
            for v, ts in sorted(buckets.items())
        ]
        out.append({"key": key, "title": title, "rows": rows})
    return out


def walk_forward(trades: list[dict], control: list[dict], min_n: int = 10) -> dict:
    """逐年滚动：只用此前年份的交易挑出跑赢同市场状态随机组的"信号 × 市场状态"组合，在当年检验。"""
    year = lambda t: t["entry_date"].year  # noqa: E731
    years = sorted({year(t) for t in trades})
    folds, picked = [], []
    for y in years[1:]:
        train = [t for t in trades if year(t) < y]
        ctrl_avg: dict[str, list[float]] = {}
        for t in control:
            if year(t) < y:
                ctrl_avg.setdefault(_regime(t), []).append(t["r_multiple"])
        units: dict[str, list[float]] = {}
        for t in train:
            units.setdefault(_unit(t), []).append(t["r_multiple"])
        selected = sorted(
            u for u, rs in units.items()
            if len(rs) >= min_n and len(ctrl_avg.get(u.split("|")[1], [])) >= 5
            and sum(rs) / len(rs) > sum(ctrl_avg[u.split("|")[1]]) / len(ctrl_avg[u.split("|")[1]])
        )
        test = [t for t in trades if year(t) == y]
        ctest = [t for t in control if year(t) == y]
        sel = [t for t in test if _unit(t) in selected]
        picked.extend(sel)
        folds.append({
            "year": y, "train_trades": len(train), "selected": [_unit_name(u) for u in selected],
            "test_all": _stat(test), "edge_all": matched_edge(test, ctest),
            "test_selected": _stat(sel), "edge_selected": matched_edge(sel, ctest),
        })
    test_years = set(years[1:])
    ctrl_test = [t for t in control if year(t) in test_years]
    return {
        "min_n": min_n,
        "folds": folds,
        "overall": {"selected": _stat(picked), "edge": matched_edge(picked, ctrl_test, _year_regime)},
    }


def _by(trades: list[dict], key: str, values: list[str], names: dict[str, str]) -> dict:
    return {v: {**_stat([t for t in trades if t[key] == v]), "name": names.get(v, v)} for v in values}


def suggest_weights(by_sig: dict) -> dict:
    avgs = {k: v.get("avg_r") for k, v in by_sig.items() if v.get("trades", 0) >= 10}
    if not avgs:
        return {}
    span = max(abs(x) for x in avgs.values()) or 1.0
    return {k: round(max(0.2, min(1.0, 0.6 + 0.4 * v / span)), 2) for k, v in avgs.items()}


def summarize(trades: list[dict], control: list[dict] | None = None) -> tuple[dict, dict, dict]:
    """返回（汇总，按信号，建议权重）。建议权重只基于样本内交易。"""
    sig_values = ["B1", "B2", "B3"]
    by_sig = _by(trades, "signal_type", sig_values, TYPE_NAMES)
    summary = _stat(trades)
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    summary["exit_reasons"] = reasons
    summary["by_scope"] = _by(trades, "scope", ["bi", "seg"], {"bi": "笔级别", "seg": "线段级别"})
    ins = [t for t in trades if t.get("segment") == "in"]
    outs = [t for t in trades if t.get("segment") == "out"]
    summary["in_sample"] = {**_stat(ins), "by_signal": _by(ins, "signal_type", sig_values, TYPE_NAMES)}
    summary["out_sample"] = {**_stat(outs), "by_signal": _by(outs, "signal_type", sig_values, TYPE_NAMES)}
    if control is not None:
        summary["control"] = _stat(control)
        summary["edge_ci"] = bootstrap_edge([t["r_multiple"] for t in trades], [t["r_multiple"] for t in control])
        if all("regime" in t.get("tags", {}) for t in trades + control):
            summary["matched_edge"] = matched_edge(trades, control, _year_regime)
            summary["diagnostics"] = diagnose(trades, control)
            summary["walk_forward"] = walk_forward(trades, control)
    summary["note"] = SURVIVORSHIP_NOTE
    suggested = suggest_weights(summary["in_sample"]["by_signal"] if ins else by_sig)
    return summary, by_sig, suggested


# ---------- 运行 ----------

WORKERS = max(1, min(6, (os.cpu_count() or 2) - 2))


def _series_worker(args: tuple) -> list[dict]:
    code, bars, params, kwargs, rng_seed = args
    try:
        return backtest_series(code, bars, params, rng=random.Random(rng_seed), **kwargs)
    except Exception:
        logger.exception("回测 %s 失败", code)
        return []


def _run_parallel(tasks: list[tuple], on_done) -> list[dict]:
    """多进程逐只回测；每只股票的随机数种子由代码决定，结果与并行顺序无关。"""
    out: list[dict] = []
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(_series_worker, t) for t in tasks]
        for i, f in enumerate(as_completed(futures)):
            out.extend(f.result())
            on_done(i + 1, len(out))
    out.sort(key=lambda t: (t["entry_date"], t["code"]))
    return out



def _pick_codes(sample_size: int, codes: list[str] | None, seed: int) -> list[str]:
    if codes:
        return codes
    # 从全部在市股票（含 ST）中抽样，不预先按基本面筛选，减少选择偏差
    with session_scope() as db:
        pool = sorted(db.execute(select(StockBasic.code).where(StockBasic.is_index.is_(False), StockBasic.active.is_(True))).scalars())
    random.Random(seed).shuffle(pool)
    return pool[:sample_size]


def _attach_bench(trades: list[dict], bench: dict[date, float], bench_dates: list[date], split_date: date,
                  regimes: dict[date, str] | None = None) -> None:
    def close_on(d: date) -> float | None:
        if d in bench:
            return bench[d]
        i = bisect.bisect_right(bench_dates, d)
        return bench[bench_dates[i - 1]] if i else None

    for t in trades:
        b0, b1 = close_on(t["entry_date"]), close_on(t["exit_date"])
        t["bench_ret"] = (b1 / b0 - 1) * 100 if b0 and b1 else None
        t["excess"] = t["pnl_pct"] - t["bench_ret"] if t["bench_ret"] is not None else None
        t["segment"] = "in" if t["entry_date"] < split_date else "out"
        if regimes is not None:
            # 入场在次日开盘，市场状态取入场日之前最后一个交易日，避免用到当天收盘
            i = bisect.bisect_left(bench_dates, t["entry_date"])
            t.setdefault("tags", {})["regime"] = regimes.get(bench_dates[i - 1], "unknown") if i else "unknown"


EXPERIMENT_KEYS = {"target_cap_r", "rr_filter", "min_reward_risk", "hard_stop_pct", "time_stop_bars"}


async def run_backtest(ctx: JobContext, sample_size: int = 50, codes: list[str] | None = None, lookback_bars: int = 750,
                       entry_mode: str = "confirmed", seed: int = 42, split_date: date | None = None,
                       slippage: float | None = None, with_control: bool = True, overrides: dict | None = None,
                       label: str | None = None, experiment: str | None = None) -> dict:
    params = get_active_params()
    if slippage is not None:
        params["slippage"] = slippage
    overrides = {k: v for k, v in (overrides or {}).items() if k in EXPERIMENT_KEYS}
    params.update(overrides)
    use_rr_filter = bool(params.pop("rr_filter", True))
    codes = _pick_codes(sample_size, codes, seed)
    bench_bars = load_bars(KlineDaily, BENCH_CODE, lookback_bars + 350)
    bench = {b.dt: b.close for b in bench_bars}
    regimes = index_regime_map(bench_bars)
    bench_dates = sorted(bench)
    if split_date is None:
        tail = bench_dates[350:] or bench_dates
        split_date = tail[int(len(tail) * params.get("backtest_split_ratio", 0.7))] if tail else date.today()
    with session_scope() as db:
        st_codes = set(db.execute(select(StockBasic.code).where(StockBasic.is_st.is_(True))).scalars())

    run_params = {
        "sample_size": len(codes), "lookback_bars": lookback_bars, "entry_mode": entry_mode, "seed": seed,
        "split_date": split_date.isoformat(), "slippage": params["slippage"], "with_control": with_control,
        "label": label, "overrides": overrides, "experiment": experiment,
        "strategy": {k: params[k] for k in ("hard_stop_pct", "min_reward_risk", "time_stop_bars", "batch_ratios", "signal_recent_bars")},
    }
    with session_scope() as db:
        run = BacktestRun(status="running", params=run_params)
        db.add(run)
        db.flush()
        run_id = run.id
    total_steps = len(codes) * (2 if with_control else 1)
    ctx.update(done=0, total=total_steps, message=f"回测 {len(codes)} 只股票", force=True)

    series: dict[str, list] = {}
    tag = f"{label}：" if label else ""

    def _signals_pass() -> list[dict]:
        for code in codes:
            series[code] = load_bars(KlineDaily, code, lookback_bars + 250)
        kw = {"warmup": 250, "entry_mode": entry_mode, "use_rr_filter": use_rr_filter}
        tasks = [(c, b, params, {**kw, "is_st": c in st_codes}, f"{seed}-{c}") for c, b in series.items() if len(b) >= 400]
        return _run_parallel(tasks, lambda i, n: ctx.update(done=i, message=f"{tag}信号回测 {i}/{len(tasks)}，累计 {n} 笔"))

    def _control_pass(spec: _ControlSpec) -> list[dict]:
        tasks = [(c, b, params, {"warmup": 250, "is_st": c in st_codes, "control": spec}, f"{seed}-ctrl-{c}")
                 for c, b in series.items() if len(b) >= 400]
        return _run_parallel(tasks, lambda i, n: ctx.update(done=len(codes) + i, message=f"{tag}随机对照 {i}/{len(tasks)}，累计 {n} 笔"))

    try:
        trades = await asyncio.to_thread(_signals_pass)
        control: list[dict] | None = None
        if with_control and trades:
            flat_bars = sum(max(0, len(b) - 250) for b in series.values()) or 1
            spec = _ControlSpec(
                prob=min(0.5, len(trades) / flat_bars),
                stop_pct=statistics.median(t["stop_pct"] for t in trades),
                rr=statistics.median(t["rr"] for t in trades if t["rr"]) if any(t["rr"] for t in trades) else params["min_reward_risk"],
            )
            control = await asyncio.to_thread(_control_pass, spec)
            run_params["control_spec"] = spec.__dict__
        _attach_bench(trades, bench, bench_dates, split_date, regimes)
        if control:
            _attach_bench(control, bench, bench_dates, split_date, regimes)
        summary, by_sig, suggested = summarize(trades, control)
        cols = {c.name for c in BacktestTrade.__table__.columns} - {"id", "run_id"}
        with session_scope() as db:
            for t in trades + (control or []):
                db.add(BacktestTrade(run_id=run_id, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in t.items() if k in cols}))
            run = db.get(BacktestRun, run_id)
            run.status, run.summary, run.by_signal, run.suggested_weights = "success", summary, by_sig, suggested
            run.params = run_params
            run.finished_at = datetime.now()
            run.message = f"{label + '：' if label else ''}{len(codes)} 只股票，信号交易 {len(trades)} 笔，对照 {len(control or [])} 笔"
    except Exception as exc:
        with session_scope() as db:
            run = db.get(BacktestRun, run_id)
            run.status, run.message, run.finished_at = "failed", str(exc)[:1000], datetime.now()
        raise
    ctx.update(message=f"回测完成：信号 {len(trades)} 笔，对照 {len(control or [])} 笔", force=True)
    return {"run_id": run_id, "trades": len(trades), "summary": {k: summary.get(k) for k in ("trades", "avg_r", "win_rate")}}


EXIT_VARIANTS = [
    ("基线：盈亏比过滤 + 缠论目标", {}),
    ("取消盈亏比过滤", {"rr_filter": False}),
    ("目标一封顶 2R", {"target_cap_r": 2.0}),
    ("取消过滤 + 目标一封顶 2R", {"rr_filter": False, "target_cap_r": 2.0}),
    ("取消过滤 + 目标一封顶 1.5R", {"rr_filter": False, "target_cap_r": 1.5}),
]


async def run_exit_experiment(ctx: JobContext, sample_size: int = 1000, lookback_bars: int = 950, seed: int = 2026) -> dict:
    """同一批股票、同一随机种子下比较几组出场规则，每组存为一条回测记录。"""
    experiment = f"exit-{datetime.now():%Y%m%d%H%M}"
    codes = _pick_codes(sample_size, None, seed)
    run_ids = []
    for label, overrides in EXIT_VARIANTS:
        res = await run_backtest(ctx, codes=codes, lookback_bars=lookback_bars, seed=seed, overrides=overrides,
                                 label=label, experiment=experiment)
        run_ids.append(res["run_id"])
    return {"experiment": experiment, "run_ids": run_ids}


def compare_runs(experiment: str | None = None) -> dict:
    """一次实验内各组的关键指标；不指定时取最近一次实验。"""
    with session_scope() as db:
        rows = db.execute(select(BacktestRun).where(BacktestRun.status == "success").order_by(BacktestRun.id.desc()).limit(200)).scalars().all()
        runs = [r for r in rows if (r.params or {}).get("experiment")]
        if experiment is None and runs:
            experiment = runs[0].params["experiment"]
        runs = sorted((r for r in runs if r.params["experiment"] == experiment), key=lambda r: r.id)
        items = []
        for r in runs:
            s = r.summary or {}
            reasons = s.get("exit_reasons") or {}
            n = s.get("trades") or 0
            items.append({
                "id": r.id, "label": r.params.get("label"), "overrides": r.params.get("overrides"),
                "trades": n, "win_rate": s.get("win_rate"), "avg_r": s.get("avg_r"), "avg_pnl_pct": s.get("avg_pnl_pct"),
                "avg_excess": s.get("avg_excess"), "profit_factor": s.get("profit_factor"),
                "avg_holding_days": s.get("avg_holding_days"),
                "target_hit_ratio": s.get("target_hit_ratio"),
                "stop_ratio": round(sum(v for k, v in reasons.items() if "止损" in k) / n, 3) if n else None,
                "matched_edge": s.get("matched_edge"),
                "out_sample": {k: (s.get("out_sample") or {}).get(k) for k in ("trades", "avg_r", "avg_excess")},
                "walk_forward": ((s.get("walk_forward") or {}).get("overall") or {}).get("edge"),
            })
    return {"experiment": experiment, "sample_size": runs[0].params.get("sample_size") if runs else None, "items": items}


def apply_suggested_weights(run_id: int, activate: bool = False) -> int:
    with session_scope() as db:
        run = db.get(BacktestRun, run_id)
        if run is None or not run.suggested_weights:
            raise ValueError("该回测没有可用的建议权重（样本内每类信号至少需要 10 笔交易）")
        suggested = dict(run.suggested_weights)
    params = get_active_params()
    params["signal_weights"] = {**params["signal_weights"], **suggested}
    return save_config(f"backtest-{run_id}-{date.today():%Y%m%d}", params, activate=activate, note=f"根据回测 #{run_id} 样本内结果调整信号权重")


def run_to_dict(r: BacktestRun, with_trades: bool = False) -> dict:
    out = {
        "id": r.id, "status": r.status, "params": r.params, "summary": r.summary, "by_signal": r.by_signal,
        "suggested_weights": r.suggested_weights, "message": r.message, "created_at": r.created_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }
    if with_trades:
        with session_scope() as db:
            rows = db.execute(
                select(BacktestTrade).where(BacktestTrade.run_id == r.id, BacktestTrade.is_control.is_(False)).order_by(BacktestTrade.entry_date)
            ).scalars().all()
            out["trades"] = [
                {"code": t.code, "signal_type": t.signal_type, "scope": t.scope, "entry_date": t.entry_date.isoformat(),
                 "entry_price": t.entry_price, "exit_date": t.exit_date.isoformat() if t.exit_date else None,
                 "exit_price": t.exit_price, "r_multiple": t.r_multiple, "pnl_pct": t.pnl_pct, "bench_ret": t.bench_ret,
                 "excess": t.excess, "segment": t.segment, "exit_reason": t.exit_reason, "holding_days": t.holding_days,
                 "tags": t.tags or {}}
                for t in rows
            ]
            ctrl = db.execute(
                select(BacktestTrade.r_multiple).where(BacktestTrade.run_id == r.id, BacktestTrade.is_control.is_(True))
            ).scalars().all()
            out["control_r"] = [round(x, 3) for x in ctrl if x is not None]
    return out
