"""L1 live test — MySQLDriver.fetch_db_profile. 读 .env.test 的 E2E_MYSQL_* 连真实库.

无 E2E_MYSQL_HOST 时 skip (CI 无外部库时不阻断)."""
from __future__ import annotations

import os

import pytest

from app.engine.drivers.mysql import MySQLDriver
from app.models import DataSource

# 注: 不挂模块级 pytestmark=live — 否则 test_get_pool_rejects_unsaved_ds (CI-safe 守卫,
# 不连真库) 会被误标 live, 将来 CI 改用 -m "not live" 时被误删. 仅给真连库的用例逐个标 live.


def _mysql_ds_from_env() -> DataSource | None:
    host = os.environ.get("E2E_MYSQL_HOST")
    if not host:
        return None
    return DataSource(
        id=None, namespace_id=0, db_type="mysql",
        host=host,
        port=int(os.environ.get("E2E_MYSQL_PORT", "3306")),
        database=os.environ.get("E2E_MYSQL_DB", ""),
        username=os.environ.get("E2E_MYSQL_USER", ""),
        password=os.environ.get("E2E_MYSQL_PASS", ""),
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_mysql_fetch_db_profile_real():
    ds = _mysql_ds_from_env()
    if ds is None:
        pytest.skip("E2E_MYSQL_HOST not set")
    driver = MySQLDriver()
    profile = await driver.fetch_db_profile(ds)
    assert profile["connected"] is True  # 连通判据 (与 version 抽取解耦)
    assert "version" in profile and profile["version"]
    assert "object_count" in profile and isinstance(profile["object_count"], int)
    assert "charset" in profile
    assert "profiled_at" in profile  # 始终有


@pytest.mark.live
@pytest.mark.asyncio
async def test_mysql_fetch_db_profile_no_cache_pollution():
    """一次性连接不污染 _pools (ds.id=None 不应进缓存)."""
    ds = _mysql_ds_from_env()
    if ds is None:
        pytest.skip("E2E_MYSQL_HOST not set")
    driver = MySQLDriver()
    await driver.fetch_db_profile(ds)
    assert None not in driver._pools  # ds.id 为 None 不进池


@pytest.mark.asyncio
async def test_get_pool_rejects_unsaved_ds():
    """ds.id is None 进 _get_pool 立即 ValueError (防缓存污染地雷)."""
    ds = DataSource(
        id=None, namespace_id=0, db_type="mysql",
        host="h", port=3306, database="d", username="u", password="p",
    )
    driver = MySQLDriver()
    with pytest.raises(ValueError, match="未落库"):
        await driver._get_pool(ds)
