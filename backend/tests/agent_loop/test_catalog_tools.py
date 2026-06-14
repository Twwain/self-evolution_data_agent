"""catalog_tools — list_databases / list_tables 契约 (真实 PG, 无 mock)."""
import json

import pytest

from app.engine.tools.catalog_tools import list_databases, list_tables
from app.models import DataSource, Namespace, SchemaCanonicalObject


async def _seed_ns(db, slug: str) -> int:
    ns = Namespace(name=slug, slug=slug)
    db.add(ns)
    await db.flush()
    return ns.id


@pytest.mark.asyncio
async def test_list_databases_returns_all_sources(db):
    ns_id = await _seed_ns(db, "t-listdb")
    db.add(DataSource(
        namespace_id=ns_id, db_type="mysql", host="h", port=3306,
        database="sales_db", username="u", password="p",
        description="订单库", db_profile_json=json.dumps(
            {"version": "8.0", "charset": "utf8mb4", "object_count": 12}),
    ))
    await db.flush()
    out = await list_databases(db=db, namespace_id=ns_id)
    assert out["count"] == 1
    d = out["databases"][0]
    assert d["db_type"] == "mysql"
    assert d["database"] == "sales_db"
    assert d["description"] == "订单库"
    assert d["db_profile"]["object_count"] == 12


@pytest.mark.asyncio
async def test_list_databases_empty_ns_no_error(db):
    ns_id = await _seed_ns(db, "t-listdb-empty")
    out = await list_databases(db=db, namespace_id=ns_id)
    assert out == {"databases": [], "count": 0}


@pytest.mark.asyncio
async def test_list_tables_with_data(db):
    ns_id = await _seed_ns(db, "t-listtbl")
    db.add(SchemaCanonicalObject(
        namespace_id=ns_id, db_type="mysql", database="sales_db",
        target="orders", description="订单主表",
        fields_json=json.dumps([{"name": "id"}, {"name": "amount"}]),
        indexes_json="[]", reviewed=True,
    ))
    await db.flush()
    out = await list_tables(db=db, namespace_id=ns_id, database="sales_db")
    assert out["database"] == "sales_db"
    assert out["count"] == 1
    t = out["tables"][0]
    assert t["target"] == "orders"
    assert t["description"] == "订单主表"
    assert t["field_count"] == 2
    assert t["reviewed"] is True


@pytest.mark.asyncio
async def test_list_tables_empty_returns_status_and_hint(db):
    """空库返回 status=no_schema_extracted + hint (if-then 框架)."""
    ns_id = await _seed_ns(db, "t-listtbl-empty")
    # 该库有数据源但无 canonical
    db.add(DataSource(
        namespace_id=ns_id, db_type="mysql", host="h", port=3306,
        database="cold_db", username="u", password="p",
    ))
    await db.flush()
    out = await list_tables(db=db, namespace_id=ns_id, database="cold_db")
    assert out["count"] == 0
    assert out["tables"] == []
    assert out["status"] == "no_schema_extracted"
    assert "hint" in out and "cold_db" in out["hint"]
    assert "clarify_with_user" in out["hint"]  # D7: 无法判断时澄清逃逸口


@pytest.mark.asyncio
async def test_list_tables_unknown_database(db):
    """库名不在数据源列表 → status=unknown_database."""
    ns_id = await _seed_ns(db, "t-listtbl-unknown")
    out = await list_tables(db=db, namespace_id=ns_id, database="ghost_db")
    assert out["count"] == 0
    assert out["status"] == "unknown_database"
