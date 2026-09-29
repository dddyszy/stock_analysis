"""点金术组合回测：等权最多持有 N 只，收盘出信号、次日开盘成交，含手续费、印花税、滑点和一字板限制。

- 筛选条件只调用 strategies/dianjin.passes()，数值来自 analysis/value.build_series()，都只用当天已公告、已除权的数据。
- 收益按等比复权价计算（含分红再投入）；空仓资金按年化 1.5% 计息。
- 随机对照：每笔真实交易，在同一天通过筛选的股票里随机换一只、持有同样的天数，重复 200 次，看真实交易的平均收益排在第几。
- 价值池只含当前在市股票，存在幸存者偏差；财报从 2020 年起，改良版的「近三年 ROE」要到 2022 年年报公告后才完整。
"""

import logging
import math
import random
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime

import numpy as np
from sqlalchemy import select

from app.db.models import KlineDailyRaw
from app.db.session import session_scope
from app.services.ashare_rules import is_one_price_limit_down, is_one_price_limit_up, stamp_tax_rate
from app.services.jobs import JobContext
from app.services.value_data import load_series, pool_codes
from app.strategies.dianjin import VARIANTS, DianjinParams, passes

logger = logging.getLogger(__name__)

V1_START = date(2020, 6, 1)
V2_START = date(2023, 5, 4)  # 2020～2022 三份年报最晚 2023-04-30 公告
BENCHMARKS = {"sh000922": "中证红利", "sh000300": "沪深300", "sh000852": "中证1000"}
GRID_BUY = [0.80, 0.82, 0.84, 0.86, 0.88, 0.90, 0.92]
GRID_SELL = [1.08, 1.10, 1.12, 1.14, 1.16, 1.18, 1.20]
RANDOM_DRAWS = 200
TRADING_DAYS = 252
SURVIVORSHIP = "价值池只含当前在市的股票，已退市股票不在其中，结果偏乐观；中证红利为价格指数，不含分红再投入，而策略收益含分红。"


@dataclass(frozen=True)
class SimParams:
    buy_ratio: float = 0.88
    sell_ratio: float = 1.12
    max_positions: int = 10
    slippage: float = 0.001
    commission: float = 0.00025
    cash_rate: float = 0.015
    stop_loss: float | None = None  # 例如 0.2：收盘跌破买入价 20% 次日卖出
    exit_on_fail: bool = False  # 持有期间不再满足筛选条件就卖出


@dataclass
class StockData:
    code: str
    dates: list[date]
    idx: dict[date, int]
    adj_open: np.ndarray
    adj_close: np.ndarray
    ratio: np.ndarray  # 复权收盘价 / MA120
    one_up: np.ndarray
    one_down: np.ndarray
    pe: list
    dy: list
    mv: list
    roe3: list


@dataclass
class _Bar:
    dt: date
    high: float
    low: float
    close: float


def _load_stock(code: str) -> StockData | None:
    s = load_series(code)
    if s is None or len(s.dates) < 130:
        return None
    with session_scope() as db:
        hl = {d: (h, lo) for d, h, lo in db.execute(
            select(KlineDailyRaw.trade_date, KlineDailyRaw.high, KlineDailyRaw.low).where(KlineDailyRaw.code == code)
        ).all()}
    n = len(s.dates)
    one_up = np.zeros(n, dtype=bool)
    one_down = np.zeros(n, dtype=bool)
    for i in range(1, n):
        h, lo = hl.get(s.dates[i], (s.close[i], s.close[i]))
        bar = _Bar(s.dates[i], h, lo, s.close[i])
        # 历史 ST 状态未知：按 ST 的更窄幅度判断一字板
        one_up[i] = is_one_price_limit_up(bar, s.close[i - 1], code, True)
        one_down[i] = is_one_price_limit_down(bar, s.close[i - 1], code, True)
    adj_close = np.array([s.adj_close(i) for i in range(n)])
    adj_open = np.array([s.adj_open(i) for i in range(n)])
    ma = np.array([m if m else np.nan for m in s.ma120])
    return StockData(code, s.dates, {d: i for i, d in enumerate(s.dates)}, adj_open, adj_close, adj_close / ma,
                     one_up, one_down, s.pe, s.dy, s.mv, s.roe3)


def load_panel(ctx: JobContext | None = None) -> dict[str, StockData]:
    codes = pool_codes()
    panel: dict[str, StockData] = {}
    for n, code in enumerate(codes, 1):
        try:
            st = _load_stock(code)
        except Exception:  # noqa: BLE001
            logger.exception("加载回测数据失败 %s", code)
            continue
        if st is not None:
            panel[code] = st
        if ctx and n % 50 == 0:
            ctx.update(done=n, total=len(codes), message=f"加载价值池数据 {n}/{len(codes)}")
    return panel


@dataclass
class Universe:
    """某个筛选版本下，每天通过筛选的股票及其 MA120 比例。"""

    by_day: dict[date, list[tuple[float, str]]] = field(default_factory=dict)  # 按比例升序
    passed: dict[str, np.ndarray] = field(default_factory=dict)


def build_universe(panel: dict[str, StockData], p: DianjinParams, start: date) -> Universe:
    uni = Universe()
    days: dict[date, list[tuple[float, str]]] = defaultdict(list)
    for code, st in panel.items():
        ok = np.zeros(len(st.dates), dtype=bool)
        first = bisect_left(st.dates, start)
        for i in range(first, len(st.dates)):
            if passes(p, st.pe[i], st.dy[i], st.mv[i], st.roe3[i]) and not math.isnan(st.ratio[i]):
                ok[i] = True
                days[st.dates[i]].append((float(st.ratio[i]), code))
        uni.passed[code] = ok
    uni.by_day = {d: sorted(v) for d, v in days.items()}
    return uni


def trading_calendar(panel: dict[str, StockData], start: date) -> list[date]:
    days: set[date] = set()
    for st in panel.values():
        days.update(d for d in st.dates if d >= start)
    return sorted(days)


def simulate(panel: dict[str, StockData], uni: Universe, cal: list[date], sp: SimParams) -> dict:
    cash = 1.0
    pos: dict[str, dict] = {}
    pending_sell: dict[str, str] = {}
    pending_buy: list[str] = []
    trades: list[dict] = []
    nav: list[float] = []
    invested: list[float] = []
    last_close: dict[str, float] = {}
    prev_nav = 1.0
    daily_cash = (1 + sp.cash_rate) ** (1 / TRADING_DAYS) - 1
    for k, d in enumerate(cal):
        cash *= 1 + daily_cash
        # 开盘：先卖后买（前一天收盘决定）
        for code, reason in list(pending_sell.items()):
            st = panel[code]
            i = st.idx.get(d)
            if i is None or st.one_down[i]:
                continue  # 停牌或一字跌停，顺延
            p = pos.pop(code)
            px = st.adj_open[i] * (1 - sp.slippage)
            proceeds = p["qty"] * px * (1 - sp.commission - stamp_tax_rate(d))
            cash += proceeds
            del pending_sell[code]
            trades.append({"code": code, "entry_date": p["entry_date"], "exit_date": d, "entry_k": p["entry_k"], "exit_k": k,
                           "ret": proceeds / p["cost"] - 1, "reason": reason})
        target = prev_nav / sp.max_positions
        for code in pending_buy:
            if len(pos) >= sp.max_positions:
                break
            st = panel[code]
            i = st.idx.get(d)
            if i is None or st.one_up[i] or code in pos:
                continue
            amount = min(target, cash)
            if amount < target * 0.5:
                break
            px = st.adj_open[i] * (1 + sp.slippage)
            pos[code] = {"qty": amount * (1 - sp.commission) / px, "cost": amount, "entry_px": px, "entry_date": d, "entry_k": k}
            cash -= amount
        pending_buy = []
        # 收盘：估值并决定次日的买卖
        value = 0.0
        for code, p in pos.items():
            st = panel[code]
            i = st.idx.get(d)
            if i is not None:
                last_close[code] = st.adj_close[i]
            value += p["qty"] * last_close.get(code, p["entry_px"])
            if i is None or code in pending_sell:
                continue
            r = st.ratio[i]
            if not math.isnan(r) and r > sp.sell_ratio:
                pending_sell[code] = "涨破卖出线"
            elif sp.stop_loss and st.adj_close[i] <= p["entry_px"] * (1 - sp.stop_loss):
                pending_sell[code] = f"跌破买入价 {sp.stop_loss:.0%}"
            elif sp.exit_on_fail and not uni.passed[code][i]:
                pending_sell[code] = "不再满足筛选条件"
        prev_nav = cash + value
        nav.append(prev_nav)
        invested.append(value / prev_nav if prev_nav else 0.0)
        free = sp.max_positions - len(pos) + len(pending_sell)
        if free > 0:
            pending_buy = [c for r, c in uni.by_day.get(d, []) if r < sp.buy_ratio and c not in pos][: free * 2]
    open_trades = [{"code": c, "entry_date": p["entry_date"], "entry_k": p["entry_k"],
                    "ret": p["qty"] * last_close.get(c, p["entry_px"]) / p["cost"] - 1} for c, p in pos.items()]
    return {"nav": nav, "invested": invested, "trades": trades, "open": open_trades}


def _max_drawdown(values: list[float]) -> float:
    peak, mdd = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd


def _cagr(values: list[float]) -> float | None:
    if len(values) < 2 or values[0] <= 0:
        return None
    return (values[-1] / values[0]) ** (TRADING_DAYS / (len(values) - 1)) - 1


def metrics(cal: list[date], res: dict) -> dict:
    nav = res["nav"]
    rets = np.diff(np.array([1.0] + nav)) / np.array([1.0] + nav[:-1])
    trades = res["trades"]
    split = int(len(nav) * 0.7)
    yearly: dict[int, float] = {}
    prev = 1.0
    for y in sorted({d.year for d in cal}):
        idx = [i for i, d in enumerate(cal) if d.year == y]
        end = nav[idx[-1]]
        yearly[y] = round((end / prev - 1) * 100, 2)
        prev = end
    return {
        "start": cal[0].isoformat(), "end": cal[-1].isoformat(), "days": len(cal),
        "cagr": _pct(_cagr([1.0] + nav)), "total": _pct(nav[-1] - 1), "max_drawdown": _pct(_max_drawdown([1.0] + nav)),
        "sharpe": round(float(rets.mean() / rets.std() * math.sqrt(TRADING_DAYS)), 2) if rets.std() > 0 else None,
        "exposure": _pct(float(np.mean(res["invested"]))),
        "trades": len(trades), "open_positions": len(res["open"]),
        "win_rate": _pct(sum(t["ret"] > 0 for t in trades) / len(trades)) if trades else None,
        "avg_trade": _pct(sum(t["ret"] for t in trades) / len(trades)) if trades else None,
        "avg_hold_days": round(sum(t["exit_k"] - t["entry_k"] for t in trades) / len(trades), 1) if trades else None,
        "in_sample_cagr": _pct(_cagr([1.0] + nav[:split])), "out_sample_cagr": _pct(_cagr(nav[split - 1 :])) if split > 1 else None,
        "out_sample_start": cal[split].isoformat() if split < len(cal) else None,
        "yearly": yearly,
    }


def _pct(v: float | None) -> float | None:
    return None if v is None else round(v * 100, 2)


def random_control(panel: dict[str, StockData], uni: Universe, cal: list[date], trades: list[dict], sp: SimParams,
                   draws: int = RANDOM_DRAWS, seed: int = 2026) -> dict | None:
    """每笔真实交易换成同一天通过筛选的随机一只股票，持有同样的交易日数。"""
    if not trades:
        return None
    rng = random.Random(seed)
    real = sum(t["ret"] for t in trades) / len(trades)
    means = []
    for _ in range(draws):
        rs = []
        for t in trades:
            signal_day = cal[t["entry_k"] - 1] if t["entry_k"] > 0 else cal[0]
            pool = uni.by_day.get(signal_day) or []
            hold = t["exit_k"] - t["entry_k"]
            for _try in range(5):
                if not pool:
                    break
                _, code = pool[rng.randrange(len(pool))]
                st = panel[code]
                i = st.idx.get(t["entry_date"])
                if i is None or i + hold >= len(st.dates):
                    continue
                buy = st.adj_open[i] * (1 + sp.slippage) / (1 - sp.commission)
                sell = st.adj_open[i + hold] * (1 - sp.slippage) * (1 - sp.commission - stamp_tax_rate(st.dates[i + hold]))
                rs.append(sell / buy - 1)
                break
        if rs:
            means.append(sum(rs) / len(rs))
    if not means:
        return None
    means.sort()
    pct = sum(m < real for m in means) / len(means)
    return {"real_avg": _pct(real), "random_median": _pct(means[len(means) // 2]),
            "random_p5": _pct(means[int(len(means) * 0.05)]), "random_p95": _pct(means[int(len(means) * 0.95) - 1]),
            "percentile": round(pct * 100, 1), "draws": len(means)}


def benchmark_curves(cal: list[date]) -> dict:
    """基准指数从腾讯公开接口拉取不复权日线（指数没有复权），对齐到回测日历。"""
    import asyncio

    from app.providers.tencent_public import TencentPublicQuotes

    async def fetch() -> dict:
        pub = TencentPublicQuotes()
        try:
            out = {}
            for code in BENCHMARKS:
                bars, _ = await pub.kline_events(code, cal[0], cal[-1], "")
                out[code] = {b.dt: b.close for b in bars}
            return out
        finally:
            await pub.close()

    raw = asyncio.run(fetch())
    curves = {}
    for code, closes in raw.items():
        series, last = [], None
        for d in cal:
            last = closes.get(d, last)
            series.append(last)
        first = next((v for v in series if v), None)
        if first:
            series = [(v or first) / first for v in series]
            curves[code] = series
    return curves


def _bench_stats(curve: list[float]) -> dict:
    return {"cagr": _pct(_cagr(curve)), "total": _pct(curve[-1] / curve[0] - 1), "max_drawdown": _pct(_max_drawdown(curve))}


def _downsample(cal: list[date], series: dict[str, list[float]], step: int = 5) -> dict:
    keep = list(range(0, len(cal), step))
    if keep[-1] != len(cal) - 1:
        keep.append(len(cal) - 1)
    return {"dates": [cal[i].isoformat() for i in keep], **{k: [round(v[i], 4) for i in keep] for k, v in series.items()}}


STUDY_VARIANTS = [
    # (键, 名称, 筛选参数, 起始日)
    ("v2", "改良版", VARIANTS["dianjin_v2"], V2_START),
    ("v1", "原版", VARIANTS["dianjin_v1"], V1_START),
    ("v1_short", "原版（与改良版同期）", VARIANTS["dianjin_v1"], V2_START),
    ("roe_only", "原版 + ROE（股息率仍 3%）", replace(VARIANTS["dianjin_v2"], key="roe_only", min_dy=3.0), V2_START),
    ("dy_only", "原版 + 股息率 4%（不看 ROE）", replace(VARIANTS["dianjin_v1"], key="dy_only", min_dy=4.0), V2_START),
]
EXIT_VARIANTS = [
    ("base", "基线：涨破卖出线才卖", SimParams()),
    ("exit_on_fail", "不再满足筛选条件就卖", SimParams(exit_on_fail=True)),
    ("stop20", "跌破买入价 20% 止损", SimParams(stop_loss=0.2)),
]


def run_study(ctx: JobContext | None = None) -> dict:
    panel = load_panel(ctx)
    if ctx:
        ctx.update(message=f"价值池 {len(panel)} 只，开始回测", force=True)
    out: dict = {"computed_at": datetime.now().isoformat(timespec="seconds"), "stocks": len(panel), "caveat": SURVIVORSHIP, "variants": {}}
    universes: dict[str, Universe] = {}
    cals: dict[date, list[date]] = {}
    bench_cache: dict[date, dict] = {}
    for key, name, p, start in STUDY_VARIANTS:
        if ctx:
            ctx.update(message=f"回测 {name}", force=True)
        uni = build_universe(panel, p, start)
        universes[key] = uni
        cal = cals.setdefault(start, trading_calendar(panel, start))
        bench = bench_cache.get(start)
        if bench is None:
            try:
                bench = benchmark_curves(cal)
            except Exception:  # noqa: BLE001
                logger.exception("基准指数拉取失败")
                bench = {}
            bench_cache[start] = bench
        exits = {}
        for ekey, ename, sp in EXIT_VARIANTS if key in ("v1", "v2") else EXIT_VARIANTS[:1]:
            res = simulate(panel, uni, cal, sp)
            m = metrics(cal, res)
            m["name"] = ename
            if ekey == "base":
                m["random"] = random_control(panel, uni, cal, res["trades"], sp)
                m["curve"] = _downsample(cal, {"strategy": res["nav"], **bench})
                m["reasons"] = dict(sorted(((r, sum(1 for t in res["trades"] if t["reason"] == r)) for r in {t["reason"] for t in res["trades"]}), key=lambda x: -x[1]))
            exits[ekey] = m
        out["variants"][key] = {
            "name": name, "rules": p.rules, "start": start.isoformat(), "exits": exits,
            "benchmarks": {BENCHMARKS[c]: _bench_stats(v) for c, v in bench.items()},
        }
    for key in ("v2", "v1"):
        if ctx:
            ctx.update(message=f"参数热力图 {out['variants'][key]['name']}", force=True)
        start = V2_START if key == "v2" else V1_START
        cal = cals[start]
        grid = []
        for b in GRID_BUY:
            row = []
            for s in GRID_SELL:
                m = metrics(cal, simulate(panel, universes[key], cal, SimParams(buy_ratio=b, sell_ratio=s)))
                row.append({"cagr": m["cagr"], "max_drawdown": m["max_drawdown"], "trades": m["trades"]})
            grid.append(row)
        out["variants"][key]["grid"] = {"buy": GRID_BUY, "sell": GRID_SELL, "cells": grid}
    return out


def summary_text(study: dict, key: str) -> str | None:
    """策略页「回测检验」的一句话结论：只陈述数字，不做预测。"""
    v = (study.get("variants") or {}).get(key)
    if not v:
        return None
    m = v["exits"]["base"]
    bench = v["benchmarks"].get("中证红利") or {}
    rnd = m.get("random") or {}
    return (f"{v['start'][:7]} 至 {m['end'][:7]}：年化 {m['cagr']}%，最大回撤 {m['max_drawdown']}%，"
            f"同期中证红利（价格指数）年化 {bench.get('cagr')}%；{m['trades']} 笔交易平均收益 {m['avg_trade']}%，"
            f"在同一天通过筛选的随机股票中排第 {rnd.get('percentile')} 百分位；样本外年化 {m['out_sample_cagr']}%。")
