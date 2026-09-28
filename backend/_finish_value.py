import asyncio
from app.services.value_data import sync_value_finance, finance_coverage, validate_against_filters
from app.services.strategy_scan import scan_strategies

async def main():
    fin = await sync_value_finance(None, max_age_days=30, time_budget=6 * 3600)
    print("财报补拉", fin, flush=True)
    print("覆盖率", round(finance_coverage(), 4), flush=True)
    print("核对", {k: v for k, v in validate_against_filters().items()}, flush=True)
    print("策略扫描", await scan_strategies(None), flush=True)
    print("FINISH_VALUE_DONE", flush=True)
asyncio.run(main())
