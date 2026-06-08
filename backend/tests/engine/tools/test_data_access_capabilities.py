"""Verify fetch_schema / estimate_cost output includes server_capabilities.

L0 集成测试: mock driver + 验证 fetch_schema/estimate_cost 输出 server_capabilities,
当 driver.get_server_capabilities 返 None (e.g. MySQL no-op 或 buildInfo 失败) 时,
输出 dict 不应出现该 key (clean shape, 而非 None 占位).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engine.tools.data_access_tools import estimate_cost, fetch_schema


@pytest.fixture
def mock_ds():
    ds = MagicMock()
    ds.id = 99
    return ds


@pytest.fixture
def mock_driver_with_caps():
    driver = MagicMock()
    driver.fetch_schema = AsyncMock(return_value={
        "db_type": "mongodb",
        "database": "db_x",
        "target": "coll_x",
        "description": "",
        "fields": [],
        "indexes": [],
        "sample_count": 0,
    })
    driver.estimate_cost = AsyncMock(return_value={
        "estimated_rows": 100,
        "warning_level": "ok",
        "raw_explain": {},
    })
    driver.get_server_capabilities = AsyncMock(return_value={
        "version": "4.0.28",
        "agg_ops_unsupported": ["$dateTrunc", "$function", "$round"],
    })
    return driver


@pytest.mark.asyncio
async def test_fetch_schema_canonical_branch_includes_server_capabilities(
    mock_ds, mock_driver_with_caps,
):
    """canonical 命中分支也必须 attach server_capabilities."""
    canonical = MagicMock()
    canonical.fields_json = "[]"
    canonical.indexes_json = "[]"
    canonical.relationships_json = "[]"
    canonical.description = "canon-desc"
    canonical.sample_count = 0
    with patch(
        "app.engine.tools.data_access_tools.resolve_ds",
        AsyncMock(return_value=mock_ds),
    ), patch(
        "app.engine.tools.data_access_tools.get_driver",
        return_value=mock_driver_with_caps,
    ), patch(
        "app.knowledge.schema_canonical.get_schema_canonical",
        AsyncMock(return_value=canonical),
    ):
        result = await fetch_schema(
            db=MagicMock(),
            namespace_id=1,
            db_type="mongodb",
            database="db_x",
            target="coll_x",
        )
    assert result["source"] == "canonical"
    assert "server_capabilities" in result
    assert result["server_capabilities"]["version"] == "4.0.28"
    assert "$round" in result["server_capabilities"]["agg_ops_unsupported"]


@pytest.mark.asyncio
async def test_fetch_schema_includes_server_capabilities(
    mock_ds, mock_driver_with_caps,
):
    """canonical = None → introspect 路径, server_capabilities 必须 attach."""
    with patch(
        "app.engine.tools.data_access_tools.resolve_ds",
        AsyncMock(return_value=mock_ds),
    ), patch(
        "app.engine.tools.data_access_tools.get_driver",
        return_value=mock_driver_with_caps,
    ), patch(
        "app.knowledge.schema_canonical.get_schema_canonical",
        AsyncMock(return_value=None),
    ):
        result = await fetch_schema(
            db=MagicMock(),
            namespace_id=1,
            db_type="mongodb",
            database="db_x",
            target="coll_x",
        )
    assert "server_capabilities" in result
    caps = result["server_capabilities"]
    assert caps["version"] == "4.0.28"
    assert "$round" in caps["agg_ops_unsupported"]


@pytest.mark.asyncio
async def test_estimate_cost_includes_server_capabilities(
    mock_ds, mock_driver_with_caps,
):
    with patch(
        "app.engine.tools.data_access_tools.resolve_ds",
        AsyncMock(return_value=mock_ds),
    ), patch(
        "app.engine.tools.data_access_tools.get_driver",
        return_value=mock_driver_with_caps,
    ):
        result = await estimate_cost(
            db=MagicMock(),
            namespace_id=1,
            db_type="mongodb",
            database="db_x",
            target="coll_x",
            query={"filter": {}},
        )
    assert "server_capabilities" in result
    assert result["server_capabilities"]["version"] == "4.0.28"


@pytest.mark.asyncio
async def test_omits_server_capabilities_when_driver_returns_none(mock_ds):
    """MySQL driver 返 None 时, 输出不应出现 server_capabilities key (clean shape)."""
    driver = MagicMock()
    driver.fetch_schema = AsyncMock(return_value={
        "db_type": "mysql",
        "database": "db_x",
        "target": "tab_x",
        "description": "",
        "fields": [],
        "indexes": [],
        "sample_count": 0,
    })
    driver.get_server_capabilities = AsyncMock(return_value=None)
    with patch(
        "app.engine.tools.data_access_tools.resolve_ds",
        AsyncMock(return_value=mock_ds),
    ), patch(
        "app.engine.tools.data_access_tools.get_driver",
        return_value=driver,
    ), patch(
        "app.knowledge.schema_canonical.get_schema_canonical",
        AsyncMock(return_value=None),
    ):
        result = await fetch_schema(
            db=MagicMock(),
            namespace_id=1,
            db_type="mysql",
            database="db_x",
            target="tab_x",
        )
    assert "server_capabilities" not in result
