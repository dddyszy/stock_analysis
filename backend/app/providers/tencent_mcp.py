"""基于 westock-mcp 的 DataProvider。字段解析容错，真实字段以 probe 样本为准。"""

import logging
from datetime import date, timedelta
from typing import Any

from app.mcp.client import McpSession, ToolCaller
from app.mcp.errors import McpBusinessError, McpRateLimited
from app.providers.base import Bar, Breadth, DataProvider, FinanceRecord, Quote, Sector, StockRef
from app.providers.parsing import (
    find_records,
    normalize_code,
    pick,
    pick_contains,
    records_by_code,
    to_date,
    to_float,
    to_int,
)

logger = logging.getLogger(__name__)

PAGE_LIMIT = 500
KLINE_PAGE_DAYS = 600


def _bar_from_list(row: list, order: str) -> Bar | None:
    if len(row) < 5:
        return None
    d = to_date(row[0])
    if d is None:
        return None
    if order == "ochl":  # 腾讯传统顺序：日期、开、收、高、低、量
        o, c, h, lo = row[1], row[2], row[3], row[4]
    else:  # ohlc
        o, h, lo, c = row[1], row[2], row[3], row[4]
    vals = [to_float(x) for x in (o, h, lo, c)]
    if any(v is None for v in vals):
        return None
    return Bar(
        dt=d,
        open=vals[0],
        high=vals[1],
        low=vals[2],
        close=vals[3],
        volume=to_float(row[5]) if len(row) > 5 else None,
        amount=to_float(row[6]) if len(row) > 6 else None,
    )


def _consistent(bars: list[Bar]) -> bool:
    return all(b.low <= min(b.open, b.close) + 1e-6 and b.high >= max(b.open, b.close) - 1e-6 for b in bars)


def parse_bars(data: Any) -> list[Bar]:
    rows = find_records(data, prefer=("nodes", "qfqday", "qfqweek", "day", "week", "klines", "kline"))
    if not rows:
        return []
    bars: list[Bar] = []
    if isinstance(rows[0], list):
        sample = rows[: min(len(rows), 30)]
        order = "ochl"
        candidate = [b for b in (_bar_from_list(r, "ochl") for r in sample) if b]
        if not _consistent(candidate):
            order = "ohlc"
        bars = [b for b in (_bar_from_list(r, order) for r in rows) if b]
    else:
        for r in rows:
            d = to_date(pick(r, "date", "trade_date", "day", "time", "dt", "tradeDate", "日期"))
            o = to_float(pick(r, "open", "开盘", "open_price"))
            h = to_float(pick(r, "high", "最高", "high_price", "max"))
            lo = to_float(pick(r, "low", "最低", "low_price", "min"))
            c = to_float(pick(r, "close", "last", "收盘", "close_price", "price"))
            if d is None or None in (o, h, lo, c):
                continue
            bars.append(
                Bar(
                    dt=d,
                    open=o,
                    high=h,
                    low=lo,
                    close=c,
                    volume=to_float(pick(r, "volume", "vol", "成交量", "v")),
                    amount=to_float(pick(r, "amount", "turnover", "成交额", "amt")),
                )
            )
    bars.sort(key=lambda b: b.dt)
    dedup: dict[date, Bar] = {b.dt: b for b in bars}
    return list(dedup.values())


def parse_quote(code: str, r: dict) -> Quote:
    return Quote(
        code=code,
        name=pick(r, "name", "stock_name", "secName", "名称"),
        price=to_float(pick(r, "price", "last", "current", "now", "zxj", "latest", "lastPrice", "close", "最新价")),
        prev_close=to_float(
            pick(r, "prev_close", "pre_close", "preclose", "yclose", "last_close", "prevClose", "preClose", "zs", "昨收")
        ),
        open=to_float(pick(r, "open", "jk", "今开", "开盘")),
        high=to_float(pick(r, "high", "zg", "最高")),
        low=to_float(pick(r, "low", "zd", "最低")),
        volume=to_float(pick(r, "volume", "vol", "cjl", "成交量")),
        amount=to_float(pick(r, "amount", "turnover", "cje", "成交额")),
        change_pct=to_float(pick(r, "change_pct", "pct", "chg_pct", "zdf", "changePercent", "pct_chg", "涨跌幅")),
        pe_ttm=to_float(pick(r, "pe_ttm", "peTTM", "PE_TTM", "pe", "syl", "市盈率")),
        pb=to_float(pick(r, "pb", "PB", "sjl", "市净率")),
        total_mv=to_float(pick(r, "total_mv", "market_cap", "totalMarketValue", "zsz", "总市值")),
        float_mv=to_float(pick(r, "float_mv", "circulating_market_cap", "floatMarketValue", "ltsz", "流通市值")),
        dt=to_date(pick(r, "date", "trade_date", "time", "update_time", "datetime")),
        raw=r,
    )


_PERIOD_KEYS = ("EndDate", "report_date", "end_date", "period", "reportDate", "date", "报告期", "截止日期")


def _absorb_periods(rows: list, by_period: dict[date, dict]) -> None:
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = to_date(pick(r, *_PERIOD_KEYS))
        if d is not None:
            by_period.setdefault(d, {}).update(r)


def parse_finance_batch(data: Any) -> dict[str, dict[date, dict]]:
    """westock 返回 {code:0, data:{sh600519:[按报告期的记录...]}}；多次调用（利润表/资产负债表/现金流量表）可合并。"""
    inner = data.get("data", data) if isinstance(data, dict) else data
    out: dict[str, dict[date, dict]] = {}
    if isinstance(inner, dict):
        for k, v in inner.items():
            code = normalize_code(k)
            if code and code[:2] in ("sh", "sz", "bj") and isinstance(v, list):
                _absorb_periods(v, out.setdefault(code, {}))
    return out


def parse_finance(data: Any) -> list[FinanceRecord]:
    """单只股票的报表：兼容按代码分组、按报表类型分组、或直接是记录列表。"""
    batch = parse_finance_batch(data)
    if batch:
        return finance_records(next(iter(batch.values())))
    by_period: dict[date, dict] = {}
    if isinstance(data, dict):
        found = False
        for key in ("income", "balance", "cashflow", "利润表", "资产负债表", "现金流量表"):
            sub = pick(data, key)
            if sub is not None:
                _absorb_periods(find_records(sub) if not isinstance(sub, list) else sub, by_period)
                found = True
        if not found:
            _absorb_periods(find_records(data), by_period)
    else:
        _absorb_periods(find_records(data), by_period)
    return finance_records(by_period)


def finance_records(by_period: dict[date, dict]) -> list[FinanceRecord]:
    out = []
    for d, r in sorted(by_period.items()):
        out.append(
            FinanceRecord(
                report_date=d,
                revenue=to_float(pick(r, "TotalOperatingRevenue", "OperatingRevenue", "total_revenue", "revenue", "营业总收入", "营业收入"))
                or to_float(pick_contains(r, "营业总收入", "营业收入")),
                net_profit=to_float(
                    pick(r, "NPParentCompanyOwners", "parent_net_profit", "net_profit_parent", "归属于母公司所有者的净利润", "归母净利润")
                )
                or to_float(pick(r, "net_profit", "NetProfit", "净利润"))
                or to_float(pick_contains(r, "归属于母公司", "净利润", "netprofit")),
                operating_cost=to_float(pick(r, "OperatingCost", "operating_cost", "营业成本"))
                or to_float(pick_contains(r, "营业成本")),
                op_cashflow=to_float(
                    pick(r, "NetOperateCashFlow", "NetOperCashFlow", "net_operate_cash_flow", "operating_cash_flow", "经营活动产生的现金流量净额")
                )
                or to_float(pick_contains(r, "经营活动产生的现金流量净额", "netoperatecash", "netopercash", "operatingcashflow")),
                total_assets=to_float(pick(r, "TotalAssets", "total_assets", "资产总计", "总资产")),
                total_liabilities=to_float(pick(r, "TotalLiability", "total_liabilities", "total_liab", "负债合计", "总负债")),
                total_equity=to_float(
                    pick(r, "SEWithoutMI", "TotalShareholderEquity", "parent_equity", "total_equity", "归属于母公司股东权益合计", "所有者权益合计")
                ),
                goodwill=to_float(pick(r, "GoodWill", "goodwill", "商誉")),
                roe=to_float(pick(r, "ROETTM", "roe_ttm")),
                revenue_yoy=to_float(pick(r, "TORGrowRate", "OperatingRevenueGrowRate", "revenue_yoy")),
                profit_yoy=to_float(pick(r, "NPParentCompanyYOY", "profit_yoy")),
                gross_margin=to_float(pick(r, "GrossIncomeRatio", "GrossProfitMargin", "gross_margin")),
                debt_ratio=to_float(pick(r, "DebtAssetsRatio", "debt_ratio")),
                raw=r,
            )
        )
    return out


def parse_breadth(data: Any) -> Breadth:
    src = data if isinstance(data, dict) else {}
    flat_src: dict = {}

    def _flatten(o: Any, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    _flatten(v, depth + 1)
                else:
                    flat_src.setdefault(k, v)
        elif isinstance(o, list):
            for v in o[:50]:
                _flatten(v, depth + 1)

    _flatten(src)
    return Breadth(
        up=to_int(pick(flat_src, "up", "up_count", "rise", "rise_count", "zjs", "上涨", "上涨家数")),
        down=to_int(pick(flat_src, "down", "down_count", "fall", "fall_count", "djs", "下跌", "下跌家数")),
        flat=to_int(pick(flat_src, "flat", "flat_count", "pjs", "平盘", "平盘家数")),
        limit_up=to_int(pick(flat_src, "up_limit_count", "limit_up", "zt", "zt_count", "涨停", "涨停家数")),
        limit_down=to_int(pick(flat_src, "down_limit_count", "limit_down", "dt", "dt_count", "跌停", "跌停家数")),
        amount=to_float(pick(flat_src, "amount", "turnover", "total_amount", "成交额", "两市成交额")),
        raw=src if isinstance(src, dict) else {"data": data},
    )


class TencentMcpProvider(DataProvider):
    name = "mcp"

    def __init__(self, caller: ToolCaller | None = None) -> None:
        self._own = caller is None
        self.caller: ToolCaller = caller or McpSession()

    async def close(self) -> None:
        if self._own and hasattr(self.caller, "close"):
            await self.caller.close()

    async def _call(self, tool: str, args: dict | None = None) -> Any:
        return await self.caller.call(tool, args or {})

    async def _paged(self, tool: str, args: dict, limit: int = PAGE_LIMIT, max_pages: int = 40) -> list:
        out: list = []
        for page in range(max_pages):
            data = await self._call(tool, {**args, "limit": limit, "offset": page * limit})
            rows = find_records(data)
            out.extend(rows)
            if len(rows) < limit:
                break
        return out

    async def list_industries(self) -> list[Sector]:
        data = await self._call("data_sector", {"mode": "list", "scope": "sw1", "limit": 200})
        out = []
        for r in find_records(data):
            code = pick(r, "code", "sector_code", "id", "bk_code")
            name = pick(r, "name", "sector_name", "bk_name")
            if code and name:
                out.append(Sector(code=str(code), name=str(name), change_pct=to_float(pick(r, "change_pct", "zdf", "pct")), raw=r))
        return out

    async def sector_constituents(self, sector_code: str) -> list[StockRef]:
        # data_sector 的成份股返回里 code 字段全是板块代码（上游数据问题），
        # 改用 tool_filter 以板块为 universe 取成份股；两个宽松条件取并集，覆盖无 PE 的股票
        seen: dict[str, StockRef] = {}
        for expr in ("PE_TTM > -100000000", "PB > -100000000"):
            try:
                data = await self._call("tool_filter", {"expression": expr, "universe": sector_code, "limit": 3000})
            except McpBusinessError:
                continue
            for r in find_records(data, prefer=("stocks",)):
                code = normalize_code(pick(r, "code", "symbol", "stock_code")) if isinstance(r, dict) else None
                if code and code[:2] in ("sh", "sz", "bj") and code not in seen:
                    seen[code] = StockRef(code=code, name=str(pick(r, "name", "stock_name", default=code)))
        return list(seen.values())

    async def st_codes(self) -> set[str]:
        return await self.label_codes("risk_st")

    async def label_codes(self, label: str) -> set[str]:
        rows = await self._paged("tool_label", {"names": label})
        return {c for c in (normalize_code(pick(r, "code", "symbol")) for r in rows if isinstance(r, dict)) if c}

    async def event_codes(self, event: str) -> set[str]:
        rows = await self._paged("tool_event", {"names": event})
        return {c for c in (normalize_code(pick(r, "code", "symbol")) for r in rows if isinstance(r, dict)) if c}

    async def list_date(self, code: str) -> date | None:
        data = await self._call("data_profile", {"code": code})
        src = data if isinstance(data, dict) else {}
        d = to_date(pick(src, "list_date", "ipo_date", "listing_date", "上市日期", "ssrq", "listDate"))
        if d is None:
            d = to_date(pick_contains(src, "上市日期", "listdate", "ipodate"))
        return d

    async def kline(
        self, code: str, period: str = "day", start: date | None = None, end: date | None = None, fq: str = "qfq"
    ) -> list[Bar]:
        end = end or date.today()
        if start is None:
            data = await self._call("data_kline", {"code": code, "period": period, "fq": fq or None, "limit": 500})
            return parse_bars(data)
        bars: dict[date, Bar] = {}
        cursor_end = end
        span = timedelta(days=KLINE_PAGE_DAYS if period == "day" else KLINE_PAGE_DAYS * 5)
        while cursor_end >= start:
            cursor_start = max(start, cursor_end - span)
            data = await self._call(
                "data_kline",
                {
                    "code": code,
                    "period": period,
                    "fq": fq or None,
                    "start": cursor_start.isoformat(),
                    "end": cursor_end.isoformat(),
                    "limit": 2000,
                },
            )
            page = parse_bars(data)
            for b in page:
                if start <= b.dt <= end:
                    bars[b.dt] = b
            if not page:
                break
            cursor_end = cursor_start - timedelta(days=1)
        return [bars[d] for d in sorted(bars)]

    async def quotes(self, codes: list[str]) -> dict[str, Quote]:
        if not codes:
            return {}
        data = await self._call("data_quote", {"codes": ",".join(codes)})
        out = {}
        for code, rec in records_by_code(data).items():
            out[code] = parse_quote(code, rec)
        if not out and len(codes) == 1 and isinstance(data, dict):
            out[codes[0]] = parse_quote(codes[0], data)
        return out

    async def finance(self, code: str, num: int = 8) -> list[FinanceRecord]:
        return (await self.finance_batch([code], num)).get(code, [])

    async def finance_batch(self, codes: list[str], num: int = 8) -> dict[str, list[FinanceRecord]]:
        """按报表类型批量拉取再按代码、报告期合并：每批股票只需 3 次调用。"""
        merged: dict[str, dict[date, dict]] = {}
        for kind in ("income", "balance", "cashflow"):
            try:
                data = await self._call("data_finance", {"codes": ",".join(codes), "type": kind, "num": num})
            except (McpBusinessError, McpRateLimited) as exc:
                logger.warning("财报 %s 批次失败（%d 只）: %s", kind, len(codes), exc)
                continue
            for code, periods in parse_finance_batch(data).items():
                target = merged.setdefault(code, {})
                for d, rec in periods.items():
                    target.setdefault(d, {}).update(rec)
        return {code: finance_records(periods) for code, periods in merged.items()}

    async def scores(self, codes: list[str]) -> dict[str, dict[str, float | None]]:
        """腾讯诊股评分：综合、基本面、风险（越高越安全）、资金、技术。单次最多约 100 只。"""
        if not codes:
            return {}
        data = await self._call("data_score", {"codes": ",".join(codes)})
        src = data.get("scores", data) if isinstance(data, dict) else data
        out = {}
        for code, rec in records_by_code(src).items():
            cur = rec.get("cur") if isinstance(rec.get("cur"), dict) else rec
            comp = to_float(pick(cur, "CompScore", "comp_score", "score", "综合评分"))
            if comp is None:
                continue
            out[code] = {
                "comp": comp,
                "funm": to_float(pick(cur, "FunmScore", "funm_score")),
                "risk": to_float(pick(cur, "RiskScore", "risk_score")),
                "cap": to_float(pick(cur, "CapScore")),
                "tec": to_float(pick(cur, "TecScore")),
            }
        return out

    async def risks(self, codes: list[str]) -> dict[str, list[str]]:
        if not codes:
            return {}
        try:
            data = await self._call("data_risk", {"codes": ",".join(codes)})
        except (McpBusinessError, McpRateLimited):
            return {}
        out: dict[str, list[str]] = {}
        for code, rec in records_by_code(data).items():
            items = find_records(rec) or []
            labels = []
            for it in items:
                if isinstance(it, dict):
                    labels.append(str(pick(it, "type", "name", "title", "risk_type", default="风险")))
            if not labels:
                t = pick(rec, "type", "types", "risk", "risks")
                if t:
                    labels = [str(t)]
            if labels:
                out[code] = labels
        return out

    async def breadth(self) -> Breadth:
        data = await self._call("data_changedist", {"type": "0"})
        return parse_breadth(data)

    async def market_overview(self) -> dict:
        """腾讯的市场画像总评：各维度得分与状态描述。"""
        data = await self._call("data_market_overview", {"type": "summary"})
        if not isinstance(data, dict):
            return {"data": data}
        row = data.get("row") or {}
        schema = data.get("schema") or {}
        items = []
        for key, name in schema.items():
            if key.endswith("_SCORE") and key not in ("ADJ_SCORE", "RAW_SCORE"):
                status_key = key[: -len("_SCORE")] + "_STATUS"
                items.append({"name": name.replace("得分", ""), "score": row.get(key), "status": row.get(status_key)})
        return {"date": data.get("date"), "adj_score": row.get("ADJ_SCORE"), "raw_score": row.get("RAW_SCORE"), "items": items}

    async def sector_ranking(self, limit: int = 50) -> list[Sector]:
        data = await self._call("data_sector", {"mode": "ranking", "limit": limit})
        out = []
        for r in find_records(data):
            code = pick(r, "code", "sector_code", "id")
            name = pick(r, "name", "sector_name")
            if code and name:
                out.append(
                    Sector(
                        code=str(code),
                        name=str(name),
                        change_pct=to_float(pick(r, "change_pct", "zdf", "pct", "chg", "涨跌幅")),
                        raw=r,
                    )
                )
        return out

    async def trading_days(self, start: date, end: date) -> list[date]:
        days: set[date] = set()
        limit, offset = 200, 0
        for _ in range(30):
            data = await self._call(
                "data_trade_calendar",
                {"start": start.isoformat(), "end": end.isoformat(), "trading_only": True, "limit": limit, "offset": offset},
            )
            rows = find_records(data)
            if not rows:
                if isinstance(data, list):
                    rows = data
                elif isinstance(data, dict):
                    rows = pick(data, "dates", "days", "trading_days", "list", default=[]) or []
            for r in rows:
                if isinstance(r, dict):
                    flag = pick(r, "is_trading", "is_open", "trading", "isTradingDay", "is_trade_day")
                    if flag is not None and str(flag).lower() in ("0", "false", "no"):
                        continue
                    d = to_date(pick(r, "date", "trade_date", "cal_date", "day"))
                else:
                    d = to_date(r[0] if isinstance(r, list) and r else r)
                if d:
                    days.add(d)
            if len(rows) < limit:
                break
            offset += limit
        return sorted(days)
