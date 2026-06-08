"""P2.T12: fetch_schema 工具改造 — 返回 relationships + description."""
import json

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from app.engine.tools.data_access_tools import fetch_schema
from app.models import DataSource, SchemaCanonicalObject

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def setup_ns_ds(test_session, namespace_factory):
    """创建 namespace + datasource, 返回 (ns, ds)."""
    ns = await namespace_factory()
    ds = DataSource(
        namespace_id=ns.id,
        db_type="mysql",
        host="localhost",
        port=3306,
        database="test_db",
        username="x",
        password="y",
    )
    test_session.add(ds)
    await test_session.flush()
    return ns, ds


@pytest_asyncio.fixture
async def canonical_with_relationships(test_session, setup_ns_ds):
    """创建带 relationships_json 的 SchemaCanonicalObject."""
    ns, ds = setup_ns_ds
    relationships = [
        {
            "from_target": "t_order",
            "from_field": "user_id",
            "to_target": "t_user",
            "to_field": "id",
            "relation_type": "many_to_one",
        }
    ]
    sco = SchemaCanonicalObject(
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
        fields_json=json.dumps([{"name": "id", "type": "bigint"}]),
        indexes_json=json.dumps([]),
        description="订单主表",
        relationships_json=json.dumps(relationships),
        sample_count=100,
    )
    test_session.add(sco)
    await test_session.flush()
    return ns, ds, sco


async def test_fetch_schema_returns_relationships_from_canonical(
    test_session, canonical_with_relationships
):
    """canonical 路径应返回 relationships 字段."""
    ns, ds, sco = canonical_with_relationships

    result = await fetch_schema(
        db=test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
    )

    assert result["source"] == "canonical"
    assert result["relationships"] == [
        {
            "from_target": "t_order",
            "from_field": "user_id",
            "to_target": "t_user",
            "to_field": "id",
            "relation_type": "many_to_one",
        }
    ]


async def test_fetch_schema_returns_description(
    test_session, canonical_with_relationships
):
    """canonical 路径应返回 description 字段."""
    ns, ds, sco = canonical_with_relationships

    result = await fetch_schema(
        db=test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
    )

    assert result["source"] == "canonical"
    assert result["description"] == "订单主表"


async def test_fetch_schema_fallback_has_empty_relationships(test_session, setup_ns_ds):
    """driver introspect fallback 路径应返回空 relationships 数组."""
    ns, ds = setup_ns_ds

    fake_schema = {
        "target": "t_new_table",
        "database": "test_db",
        "fields": [{"name": "id", "type": "int"}],
        "indexes": [],
        "sample_count": 0,
    }

    with patch("app.engine.tools.data_access_tools.get_driver") as get_driver_mock:
        driver_mock = AsyncMock()
        driver_mock.fetch_schema = AsyncMock(return_value=fake_schema)
        get_driver_mock.return_value = driver_mock

        result = await fetch_schema(
            db=test_session,
            namespace_id=ns.id,
            db_type="mysql",
            database="test_db",
            target="t_new_table",
        )

    assert result["source"] == "introspect"
    assert result["relationships"] == []
    # 原有字段仍在
    assert result["fields"] == [{"name": "id", "type": "int"}]
    assert result["target"] == "t_new_table"
