from fastapi import APIRouter, HTTPException

from app.services import strategy_scan

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("")
def strategies() -> list[dict]:
    return strategy_scan.strategy_list()


@router.get("/backtest")
def backtest() -> dict | None:
    return strategy_scan.latest_backtest()


@router.get("/{key}/screen")
def screen(key: str, zone: str | None = None, passed_only: bool = True) -> dict:
    try:
        return strategy_scan.screen(key, zone, passed_only)
    except KeyError:
        raise HTTPException(404, f"没有这个策略：{key}")


@router.get("/stock/{code}")
def stock_views(code: str) -> list[dict]:
    return strategy_scan.stock_views(code)
