"""测试一律使用模拟库 chan_stock_mock，任何测试都不能写入真实库 chan_stock。"""

import os

import pytest

os.environ["DATA_PROVIDER"] = "mock"
os.environ["SCHEDULER_ENABLED"] = "false"


@pytest.fixture(scope="session")
def mock_db():
    """需要数据库的测试使用：确保模拟库存在并已迁移；连不上 MySQL 时跳过。"""
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.effective_db_name.endswith("_mock"), "测试必须连接模拟库"
    try:
        from app.main import migrate

        migrate()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MySQL 不可用：{exc}")
    return settings.effective_db_name
