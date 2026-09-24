"""本地合成数据，只用于未授权时开发和演示，数值没有任何真实含义。"""

import hashlib
from datetime import date, timedelta
from functools import lru_cache

import numpy as np

from app.providers.base import INDEX_CODES, Bar, Breadth, DataProvider, FinanceRecord, Quote, Sector, StockRef

SERIES_START = date(2018, 1, 2)

_INDUSTRIES = [
    ("pt01801010", "农林牧渔"), ("pt01801030", "基础化工"), ("pt01801040", "钢铁"),
    ("pt01801080", "电子"), ("pt01801110", "家用电器"), ("pt01801120", "食品饮料"),
    ("pt01801150", "医药生物"), ("pt01801160", "公用事业"), ("pt01801750", "计算机"),
    ("pt01801780", "银行"), ("pt01801730", "电力设备"), ("pt01801880", "汽车"),
]
_PER_INDUSTRY = 25


def _seed(code: str) -> int:
    return int(hashlib.md5(code.encode()).hexdigest()[:8], 16)


def _mock_universe() -> list[StockRef]:
    out = []
    n = 0
    for icode, iname in _INDUSTRIES:
        for _ in range(_PER_INDUSTRY):
            n += 1
            if n % 5 == 0:
                code = f"sz300{n:03d}"
            elif n % 3 == 0:
                code = f"sz000{n:03d}"
            else:
                code = f"sh600{n:03d}"
            out.append(StockRef(code=code, name=f"{iname}{n:03d}", industry=iname, industry_code=icode))
    return out


UNIVERSE = _mock_universe()
_BY_CODE = {s.code: s for s in UNIVERSE}


def _weekdays(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


@lru_cache(maxsize=1024)
def _series(code: str, end: date) -> tuple[Bar, ...]:
    rng = np.random.default_rng(_seed(code))
    days = _weekdays(SERIES_START, end)
    price = float(rng.uniform(5, 80)) if not code.startswith(("sh000", "sz399")) else float(rng.uniform(2000, 4000))
    bars = []
    regime_left, drift = 0, 0.0
    vol = float(rng.uniform(0.012, 0.03))
    for d in days:
        if regime_left <= 0:
            regime_left = int(rng.integers(25, 110))
            drift = float(rng.normal(0.0002, 0.0035))
        regime_left -= 1
        prev = price
        ret = drift + vol * float(rng.standard_normal())
        ret = max(min(ret, 0.099), -0.099)
        price = max(prev * (1 + ret), 0.5)
        o = prev * (1 + float(rng.normal(0, vol / 4)))
        hi = max(o, price) * (1 + abs(float(rng.normal(0, vol / 2))))
        lo = min(o, price) * (1 - abs(float(rng.normal(0, vol / 2))))
        v = float(rng.uniform(1e6, 5e7))
        bars.append(Bar(dt=d, open=round(o, 2), high=round(hi, 2), low=round(lo, 2), close=round(price, 2), volume=v, amount=v * price))
    return tuple(bars)


class MockProvider(DataProvider):
    name = "mock"

    async def list_industries(self) -> list[Sector]:
        return [Sector(code=c, name=n) for c, n in _INDUSTRIES]

    async def sector_constituents(self, sector_code: str) -> list[StockRef]:
        return [s for s in UNIVERSE if s.industry_code == sector_code]

    async def st_codes(self) -> set[str]:
        return {s.code for i, s in enumerate(UNIVERSE) if i % 37 == 0}

    async def label_codes(self, label: str) -> set[str]:
        if label == "risk_st":
            return await self.st_codes()
        salt = sum(ord(ch) for ch in label)
        return {s.code for s in UNIVERSE if (_seed(s.code) + salt) % 29 == 0}

    async def event_codes(self, event: str) -> set[str]:
        return await self.label_codes(f"event:{event}")

    async def list_date(self, code: str) -> date | None:
        return SERIES_START - timedelta(days=_seed(code) % 3000)

    async def kline(self, code, period="day", start=None, end=None, fq="qfq") -> list[Bar]:
        end = end or date.today()
        bars = [b for b in _series(code, date.today()) if (start is None or b.dt >= start) and b.dt <= end]
        if period == "week":
            from app.services.kline_utils import resample_weekly

            return resample_weekly(bars)
        return bars if start is not None else bars[-500:]

    async def quotes(self, codes: list[str]) -> dict[str, Quote]:
        out = {}
        for code in codes:
            s = _series(code, date.today())
            if len(s) < 2:
                continue
            last, prev = s[-1], s[-2]
            ref = _BY_CODE.get(code)
            rng = np.random.default_rng(_seed(code) + 7)
            out[code] = Quote(
                code=code,
                name=ref.name if ref else INDEX_CODES.get(code, code),
                price=last.close,
                prev_close=prev.close,
                open=last.open,
                high=last.high,
                low=last.low,
                volume=last.volume,
                amount=last.amount,
                change_pct=round((last.close / prev.close - 1) * 100, 2),
                pe_ttm=round(float(rng.uniform(-20, 80)), 2),
                pb=round(float(rng.uniform(0.5, 8)), 2),
                total_mv=round(float(rng.uniform(30e8, 3000e8)), 0),
                dt=last.dt,
            )
        return out

    async def finance(self, code: str, num: int = 8) -> list[FinanceRecord]:
        rng = np.random.default_rng(_seed(code) + 11)
        base_rev = float(rng.uniform(5e8, 5e10))
        growth = float(rng.normal(0.08, 0.15))
        margin = float(rng.uniform(-0.05, 0.25))
        equity = base_rev * float(rng.uniform(0.6, 2.5))
        out = []
        today = date.today()
        q_ends = []
        y = today.year
        for _ in range(3):
            for m, d in ((12, 31), (9, 30), (6, 30), (3, 31)):
                qd = date(y, m, d)
                if qd < today - timedelta(days=45):
                    q_ends.append(qd)
            y -= 1
        q_ends = sorted(q_ends)[-num:]
        for i, qd in enumerate(q_ends):
            quarter_factor = qd.month / 12
            rev = base_rev * (1 + growth) ** (i / 4) * quarter_factor
            profit = rev * (margin + float(rng.normal(0, 0.02)))
            assets = equity * float(rng.uniform(1.3, 3.0))
            out.append(
                FinanceRecord(
                    report_date=qd,
                    revenue=rev,
                    net_profit=profit,
                    operating_cost=rev * float(rng.uniform(0.5, 0.85)),
                    op_cashflow=profit * float(rng.uniform(0.3, 1.6)),
                    total_assets=assets,
                    total_liabilities=assets - equity,
                    total_equity=equity,
                    goodwill=equity * float(rng.uniform(0, 0.4)),
                )
            )
        return out

    async def scores(self, codes: list[str]) -> dict[str, dict[str, float | None]]:
        out = {}
        for c in codes:
            rng = np.random.default_rng(_seed(c) + 3)
            out[c] = {k: round(float(rng.uniform(30, 95)), 1) for k in ("comp", "funm", "risk", "cap", "tec")}
        return out

    async def risks(self, codes: list[str]) -> dict[str, list[str]]:
        return {c: ["质押比例较高"] for c in codes if _seed(c) % 23 == 0}

    async def breadth(self) -> Breadth:
        qs = await self.quotes([s.code for s in UNIVERSE])
        chg = [q.change_pct or 0 for q in qs.values()]
        return Breadth(
            up=sum(1 for c in chg if c > 0),
            down=sum(1 for c in chg if c < 0),
            flat=sum(1 for c in chg if c == 0),
            limit_up=sum(1 for c in chg if c >= 9.8),
            limit_down=sum(1 for c in chg if c <= -9.8),
            amount=sum(q.amount or 0 for q in qs.values()),
        )

    async def market_overview(self) -> dict:
        b = await self.breadth()
        return {"source": "mock", "up": b.up, "down": b.down, "amount": b.amount}

    async def sector_ranking(self, limit: int = 50) -> list[Sector]:
        qs = await self.quotes([s.code for s in UNIVERSE])
        agg: dict[str, list[float]] = {}
        for s in UNIVERSE:
            if s.code in qs:
                agg.setdefault(s.industry_code, []).append(qs[s.code].change_pct or 0)
        names = dict(_INDUSTRIES)
        out = [Sector(code=k, name=names[k], change_pct=round(float(np.mean(v)), 2)) for k, v in agg.items()]
        return sorted(out, key=lambda s: s.change_pct or 0, reverse=True)[:limit]

    async def trading_days(self, start: date, end: date) -> list[date]:
        return _weekdays(start, end)
