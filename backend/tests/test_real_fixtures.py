"""基于 probe 保存的真实响应样本校验解析器。样本不存在时跳过。"""

import asyncio
import json

import pytest

from app.mcp.client import unwrap_payload
from app.mcp.fixture import FIXTURE_DIR
from app.providers.tencent_mcp import TencentMcpProvider, parse_bars, parse_breadth, parse_finance_batch, finance_records


def _payload(name: str):
    path = FIXTURE_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"缺少样本 {name}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    payload = doc["payload"]
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        pytest.skip(f"样本 {name} 不是成功响应")
    return unwrap_payload(payload)


class _One:
    def __init__(self, data):
        self.data = data

    async def call(self, tool, args=None):
        return self.data


def test_kline_nodes_with_last_as_close():
    bars = parse_bars(_payload("data_kline__week_index"))
    assert len(bars) >= 20
    assert bars == sorted(bars, key=lambda b: b.dt)
    for b in bars:
        assert b.low <= min(b.open, b.close) + 1e-6 and b.high >= max(b.open, b.close) - 1e-6


def test_finance_income_batch():
    batch = parse_finance_batch(_payload("data_finance__income"))
    assert set(batch) >= {"sz000001"}
    recs = finance_records(batch["sz000001"])
    latest = recs[-1]
    assert latest.revenue and latest.net_profit and latest.roe is not None and latest.profit_yoy is not None


def test_breadth_counts():
    b = parse_breadth(_payload("data_changedist__default"))
    assert b.up and b.down and b.limit_up is not None and b.limit_down is not None and b.amount


def test_scores_nested_cur():
    p = TencentMcpProvider(_One(_payload("data_score__batch")))
    scores = asyncio.run(p.scores(["sh600519", "sz000001"]))
    assert scores and all(0 <= v["comp"] <= 100 and v["funm"] is not None for v in scores.values())


def test_trading_calendar_days():
    from datetime import date

    p = TencentMcpProvider(_One(_payload("data_trade_calendar__range")))
    days = asyncio.run(p.trading_days(date(2026, 1, 1), date(2026, 12, 31)))
    assert days and all(d.weekday() < 5 for d in days)


def test_st_label_and_industries():
    p = TencentMcpProvider(_One(_payload("tool_label__st")))
    st = asyncio.run(p.st_codes())
    assert len(st) > 10 and all(c[:2] in ("sh", "sz", "bj") for c in st)
    p = TencentMcpProvider(_One(_payload("data_sector__list_sw1")))
    inds = asyncio.run(p.list_industries())
    assert len(inds) >= 28 and all(i.code.startswith("pt") for i in inds)


def test_paper_history_records():
    from app.services.sim_trade import TencentMcpSimGateway

    gw = TencentMcpSimGateway.__new__(TencentMcpSimGateway)
    gw.session = _One(_payload("portfolio_paper_history__recent"))
    rows = asyncio.run(gw.history("recent"))
    assert rows and rows[0]["order_id"] and rows[0]["direction"] in ("buy", "sell") and rows[0]["status"] == "filled"


def test_paper_account():
    from app.services.sim_trade import TencentMcpSimGateway

    gw = TencentMcpSimGateway.__new__(TencentMcpSimGateway)
    gw.session = _One(_payload("portfolio_paper_portfolio__default"))
    acc = asyncio.run(gw.account())
    assert acc["total_asset"] and acc["cash"] is not None
