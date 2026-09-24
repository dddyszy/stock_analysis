import asyncio
import json
from datetime import date

from app.mcp.client import unwrap_payload
from app.mcp.errors import McpBusinessError
from app.mcp.fixture import FixtureSession
from app.providers.parsing import normalize_code, records_by_code, to_date, to_float
from app.providers.tencent_mcp import TencentMcpProvider, parse_bars, parse_finance, parse_quote


def test_normalize_code():
    assert normalize_code("600519") == "sh600519"
    assert normalize_code("000001.SZ") == "sz000001"
    assert normalize_code("830799") == "bj830799"
    assert normalize_code("SH600519") == "sh600519"
    assert normalize_code("600519.SS") == "sh600519"


def test_scalars():
    assert to_float("1,234.5") == 1234.5
    assert to_float("12.5%") == 12.5
    assert to_float("3.2亿") == 3.2e8
    assert to_float("--") is None
    assert to_date("20240102") == date(2024, 1, 2)
    assert to_date("2024-01-02 15:00:00") == date(2024, 1, 2)


def test_unwrap_business_error():
    assert unwrap_payload({"ok": True, "data": [1]}) == [1]
    try:
        unwrap_payload({"ok": False, "message": "无权限"})
    except McpBusinessError as exc:
        assert "无权限" in str(exc)
    else:
        raise AssertionError


def test_parse_bars_tencent_list_order():
    # 腾讯传统顺序：日期、开、收、高、低、量
    data = {"code": "sh600519", "qfqday": [["2024-01-02", "10", "10.5", "10.8", "9.9", "1000"], ["2024-01-03", "10.5", "10.2", "10.6", "10.1", "800"]]}
    bars = parse_bars(data)
    assert len(bars) == 2
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close) == (10, 10.8, 9.9, 10.5)


def test_parse_bars_ohlc_list_order():
    data = [["2024-01-02", 10, 10.8, 9.9, 10.5, 1000], ["2024-01-03", 10.5, 10.6, 10.1, 10.2, 800]]
    bars = parse_bars({"list": data})
    assert (bars[0].high, bars[0].low, bars[0].close) == (10.8, 9.9, 10.5)


def test_parse_bars_dicts():
    data = {"items": [{"date": "2024-01-03", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "vol": 9}, {"date": "2024-01-02", "open": 1, "high": 1.2, "low": 0.9, "close": 1}]}
    bars = parse_bars(data)
    assert [b.dt for b in bars] == [date(2024, 1, 2), date(2024, 1, 3)]
    assert bars[1].volume == 9


def test_quotes_by_code_mapping_and_list():
    by_map = records_by_code({"sh600519": {"name": "贵州茅台", "price": "1500"}, "sz000001": {"name": "平安银行", "price": "10"}})
    assert set(by_map) == {"sh600519", "sz000001"}
    by_list = records_by_code({"list": [{"code": "600519.SH", "zxj": "1500", "zs": "1490"}]})
    q = parse_quote("sh600519", by_list["sh600519"])
    assert q.price == 1500 and q.prev_close == 1490


def test_parse_finance_merges_statements():
    data = {
        "income": [{"report_date": "2024-06-30", "营业总收入": "100亿", "归属于母公司所有者的净利润": "10亿", "营业成本": "60亿"}],
        "balance": [{"report_date": "2024-06-30", "资产总计": "500亿", "负债合计": "200亿", "归属于母公司股东权益合计": "280亿"}],
        "cashflow": [{"report_date": "2024-06-30", "经营活动产生的现金流量净额": "12亿"}],
    }
    recs = parse_finance(data)
    assert len(recs) == 1
    r = recs[0]
    assert r.revenue == 100e8 and r.net_profit == 10e8 and r.total_assets == 500e8 and r.op_cashflow == 12e8


def test_provider_on_fixture_session(tmp_path):
    (tmp_path / "data_kline__day.json").write_text(
        json.dumps({"payload": {"ok": True, "data": {"qfqday": [["2024-01-02", "10", "10.5", "10.8", "9.9", "1000"]]}}}), encoding="utf-8"
    )
    p = TencentMcpProvider(FixtureSession(tmp_path))
    bars = asyncio.run(p.kline("sh600519"))
    assert len(bars) == 1 and bars[0].close == 10.5
