"""Phase 1 Task 6: refresh_mysql_canonicals 改走 candidate 通道."""
import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from unittest.mock import AsyncMock, patch

from app.knowledge.schema_canonical import refresh_mysql_canonicals
from app.models import DataSource, SchemaCanonicalCandidate, SchemaCanonicalObject

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def mysql_ds(test_session, namespace_factory):
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


async def test_refresh_writes_candidates_not_direct_canonical(test_session, mysql_ds):
    """introspect 应写 candidate, promote 后才生成 SchemaCanonicalObject."""
    ns, ds = mysql_ds

    fake_table_stub = [{"target": "t_order", "database": "test_db"}]
    fake_table_detail = {
        "target": "t_order",
        "database": "test_db",
        "fields": [
            {"name": "id", "type": "bigint", "description": "", "nullable": False},
            {"name": "status", "type": "int", "description": "订单状态", "nullable": False},
        ],
        "indexes": [{"name": "PRIMARY", "columns": ["id"], "unique": True}],
        "description": "订单表",
        "sample_count": 0,
    }

    async def fake_fetch_schema(ds_arg, target=None):
        if target is None:
            return fake_table_stub
        return fake_table_detail

    with patch("app.engine.drivers.get_driver") as get_driver_mock:
        driver_mock = AsyncMock()
        driver_mock.fetch_schema = AsyncMock(side_effect=fake_fetch_schema)
        get_driver_mock.return_value = driver_mock

        count = await refresh_mysql_canonicals(test_session, ns.id, ns.slug)

    await test_session.commit()

    # 应返回处理的表数量
    assert count == 1

    # candidate 应有: 1 table_description + 2 field_description = 3
    cands = (await test_session.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns.id
        )
    )).scalars().all()
    kinds = {c.candidate_kind for c in cands}
    assert "table_description" in kinds
    assert "field_description" in kinds
    assert len(cands) == 3  # 1 table + 2 fields


async def test_refresh_triggers_promote(test_session, mysql_ds):
    """introspect 后 promote 被自动调用, 应生成 SchemaCanonicalObject."""
    ns, ds = mysql_ds

    fake_table_stub = [{"target": "t_order", "database": "test_db"}]
    fake_table_detail = {
        "target": "t_order",
        "database": "test_db",
        "fields": [
            {"name": "status", "type": "int", "description": "状态"},
        ],
        "indexes": [],
        "description": "订单表",
        "sample_count": 0,
    }

    async def fake_fetch_schema(ds_arg, target=None):
        return fake_table_stub if target is None else fake_table_detail

    with patch("app.engine.drivers.get_driver") as get_driver_mock:
        driver_mock = AsyncMock()
        driver_mock.fetch_schema = AsyncMock(side_effect=fake_fetch_schema)
        get_driver_mock.return_value = driver_mock

        await refresh_mysql_canonicals(test_session, ns.id, ns.slug)

    await test_session.commit()

    # promote 已被自动调用, SchemaCanonicalObject 应已创建
    sco = (await test_session.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == ns.id
        )
    )).scalar_one()
    assert sco.target == "t_order"
    assert sco.description == "订单表"
    fields = json.loads(sco.fields_json)
    assert any(f["name"] == "status" and f.get("description") == "状态" for f in fields)


async def test_refresh_no_mysql_ds_returns_zero(test_session, namespace_factory):
    """namespace 下无 MySQL 数据源时返回 0."""
    ns = await namespace_factory()
    count = await refresh_mysql_canonicals(test_session, ns.id, ns.slug)
    assert count == 0
