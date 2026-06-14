"""list_schema_canonicals 加 database 过滤 — 同 ns 两库互不串."""
import pytest

from app.knowledge.schema_canonical import list_schema_canonicals
from app.models import Namespace, SchemaCanonicalObject


@pytest.mark.asyncio
async def test_database_filter_isolates_two_dbs(db):
    ns = Namespace(name="t-dbfilter", slug="t-dbfilter")
    db.add(ns)
    await db.flush()
    db.add(SchemaCanonicalObject(
        namespace_id=ns.id, db_type="mysql", database="db_a",
        target="t_a", description="", fields_json="[]", indexes_json="[]",
    ))
    db.add(SchemaCanonicalObject(
        namespace_id=ns.id, db_type="mysql", database="db_b",
        target="t_b", description="", fields_json="[]", indexes_json="[]",
    ))
    await db.flush()

    only_a = await list_schema_canonicals(db, ns.id, database="db_a")
    assert {c.target for c in only_a} == {"t_a"}

    # 不传 database → 全返回 (向后兼容)
    all_rows = await list_schema_canonicals(db, ns.id)
    assert {c.target for c in all_rows} == {"t_a", "t_b"}
