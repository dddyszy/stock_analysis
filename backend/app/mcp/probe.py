"""授权后运行一次：把用到的每个工具调用一遍，原始响应保存到 tests/fixtures/mcp/。

用法：cd backend && uv run python -m app.mcp.probe
"""

import asyncio
import json
from datetime import date, timedelta

from app.mcp.client import McpSession, extract_payload
from app.mcp.fixture import FIXTURE_DIR

TODAY = date.today()
START = (TODAY - timedelta(days=60)).isoformat()

PROBES: list[tuple[str, str, dict]] = [
    ("data_search", "default", {"query": "贵州茅台"}),
    ("data_quote", "batch", {"codes": "sh600519,sz000001,bj830799,sh000001"}),
    ("data_kline", "day", {"code": "sh600519", "period": "day", "fq": "qfq", "limit": 30}),
    ("data_kline", "range", {"code": "sh600519", "period": "day", "fq": "qfq", "start": START, "end": TODAY.isoformat()}),
    ("data_kline", "max", {"code": "sh600519", "period": "day", "fq": "qfq", "limit": 2000}),
    ("data_kline", "week_index", {"code": "sh000001", "period": "week", "limit": 30}),
    ("data_kline", "bj", {"code": "bj830799", "period": "day", "fq": "qfq", "limit": 10}),
    ("data_minute", "default", {"code": "sh600519"}),
    ("data_technical", "macd", {"code": "sh600519", "indicator": "macd"}),
    ("data_finance", "all", {"code": "sh600519", "num": 2}),
    ("data_finance", "income", {"codes": "sh600519,sz000001", "type": "income", "num": 4}),
    ("data_profile", "default", {"code": "sh600519"}),
    ("data_dividend", "default", {"code": "sh600519", "years": 3}),
    ("data_score", "batch", {"codes": "sh600519,sz000001"}),
    ("data_risk", "batch", {"codes": "sh600519,sz000001"}),
    ("data_sector", "list_sw1", {"mode": "list", "scope": "sw1", "limit": 100}),
    ("data_sector", "ranking", {"mode": "ranking", "limit": 50}),
    ("data_sector", "constituent", {"mode": "constituent", "code": "pt01801080", "limit": 500}),
    ("data_index", "constituent", {"mode": "constituent", "code": "sh000300", "limit": 500}),
    ("data_market_overview", "summary", {"type": "summary"}),
    ("data_market_overview", "all", {"type": "all"}),
    ("data_changedist", "default", {}),
    ("data_trade_calendar", "range", {"start": START, "end": TODAY.isoformat(), "trading_only": True, "limit": 100}),
    ("data_fund_flow", "batch", {"codes": "sh600519,sz000001"}),
    ("tool_list_presets", "default", {}),
    ("tool_list_labels", "default", {}),
    ("tool_label", "st", {"names": "risk_st", "limit": 500}),
    ("tool_filter", "expr", {"expression": "intersect([PE_TTM > 0, PE_TTM < 20, ROETTM > 15])", "limit": 50}),
    ("portfolio_watchlist_groups", "default", {}),
    ("portfolio_tips_query", "default", {}),
    ("portfolio_paper_portfolio", "default", {}),
    ("portfolio_paper_positions", "default", {}),
    ("portfolio_paper_profit", "default", {}),
    ("portfolio_paper_history", "recent", {"range": "recent", "limit": 20}),
]


async def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    sess = McpSession()
    try:
        tools = await sess.list_tools()
        (FIXTURE_DIR / "_tools.json").write_text(json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"工具数: {len(tools)}")
        await sess._connect()  # noqa: SLF001
        for tool, variant, args in PROBES:
            try:
                result = await sess._client.call_tool(tool, args)  # noqa: SLF001
                payload = extract_payload(result)
                ok = not result.is_error and not (isinstance(payload, dict) and payload.get("ok") is False)
            except Exception as exc:
                payload, ok = {"error": f"{type(exc).__name__}: {exc}"}, False
            doc = {"tool": tool, "args": args, "payload": payload}
            name = f"{tool}__{variant}.json"
            (FIXTURE_DIR / name).write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            size = len(json.dumps(payload, ensure_ascii=False, default=str))
            print(f"{'OK ' if ok else 'ERR'} {name} ({size} bytes)")
            await asyncio.sleep(1.5)
    finally:
        await sess.close()


if __name__ == "__main__":
    asyncio.run(main())
