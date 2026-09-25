"""接口冒烟：在 mock 模式下直接调用路由函数，验证返回结构（不依赖外网）。"""

import asyncio

import pytest


@pytest.fixture(scope="module")
def market(mock_db):
    from app.api.routers import market as m

    return m


def test_indices_live(market):
    rows = asyncio.run(market.indices_live())
    assert len(rows) == 4
    assert {"code", "name", "price", "change_pct"} <= set(rows[0])


def test_indices_series(market):
    data = market.indices_series(days=30)
    assert set(data) == {"sh000001", "sh000300", "sz399006", "sh000852"}
    for v in data.values():
        assert len(v["dates"]) == len(v["closes"])
