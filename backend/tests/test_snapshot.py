from datetime import date

import pytest
from sqlalchemy import delete, insert

D = date(2000, 1, 3)


@pytest.fixture
def seeded(mock_db):
    from app.db.models import StockBasic, StockRiskLabel, StockRiskLabelDaily, StockStatusDaily, ValuationSnapshot
    from app.db.session import session_scope

    with session_scope() as db:
        db.execute(delete(StockBasic).where(StockBasic.code.in_(["sz990001", "sz990002"])))
        db.execute(delete(StockRiskLabel).where(StockRiskLabel.code.in_(["sz990001", "sz990002"])))
        db.execute(insert(StockBasic).values([
            {"code": "sz990001", "name": "测试一", "exchange": "sz", "industry": "银行", "is_st": False, "is_index": False, "active": True},
            {"code": "sz990002", "name": "测试二", "exchange": "sz", "industry": "电子", "is_st": True, "is_index": False, "active": True},
        ]))
        db.execute(insert(StockRiskLabel).values([
            {"code": "sz990002", "label": "event:suspension_now", "name": "停牌中", "severity": "hard", "snap_date": D},
            {"code": "sz990001", "label": "fin_forecast_dec", "name": "业绩预亏预降", "severity": "penalty", "snap_date": D},
        ]))
        db.execute(insert(ValuationSnapshot).values([
            {"code": "sz990001", "trade_date": D, "pe_ttm": 8.5, "comp_score": 70},
        ]))
    yield
    with session_scope() as db:
        for model, col in ((StockStatusDaily, StockStatusDaily.trade_date), (StockRiskLabelDaily, StockRiskLabelDaily.snap_date),
                           (ValuationSnapshot, ValuationSnapshot.trade_date)):
            db.execute(delete(model).where(col == D))
        db.execute(delete(StockBasic).where(StockBasic.code.in_(["sz990001", "sz990002"])))
        db.execute(delete(StockRiskLabel).where(StockRiskLabel.code.in_(["sz990001", "sz990002"])))


def test_snapshot_roundtrip_and_fallback(seeded):
    from app.services.snapshot import save_label_snapshot, save_status_snapshot, snapshot_at, snapshot_health

    assert save_status_snapshot(D) >= 2
    assert save_label_snapshot(D) >= 2
    # 重复保存覆盖而不是叠加
    n = save_status_snapshot(D)
    assert snapshot_health(D)["status"] == n

    snap = snapshot_at(date(2000, 1, 5), codes=["sz990001", "sz990002"])
    assert snap["status_date"] == D and snap["label_date"] == D
    one, two = snap["stocks"]["sz990001"], snap["stocks"]["sz990002"]
    assert one["pe_ttm"] == 8.5 and one["comp_score"] == 70 and one["labels"][0]["name"] == "业绩预亏预降"
    assert two["is_st"] and two["suspended"]

    assert snapshot_at(date(2000, 1, 20), codes=["sz990001"])["stocks"] == {}
