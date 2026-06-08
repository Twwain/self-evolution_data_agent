"""Phase 2 Plan 04 Task 1 — EnumDictionary CRUD 5 端点回归.

覆盖: create 201 / 409 / list filter / update 自动转 manual / delete dry_run / references 反查.
"""
import json

import pytest
import pytest_asyncio

from app.models.enum_dictionary import EnumDictionary
from app.models.namespace import Namespace
from app.models.schema_canonical_object import SchemaCanonicalObject
from app.models.user import User


@pytest_asyncio.fixture
async def ns_with_enum(db) -> tuple[int, int]:
    """Create namespace + 1 code-sourced EnumDictionary. Returns (ns_id, enum_id)."""
    # ensure admin user row for FK
    existing = await db.get(User, 1)
    if not existing:
        db.add(User(id=1, username="admin", role="admin", password_hash="x"))
        await db.commit()

    ns = Namespace(name="enum_test", slug="enum_test", description="plan04")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    ed = EnumDictionary(
        namespace_id=ns.id,
        enum_class_name="OrderStatus",
        values_json=json.dumps([
            {"name": "CREATED", "db_value": 1, "description": "已创建"},
            {"name": "PAID", "db_value": 2, "description": "已支付"},
        ]),
        source="code",
        scope="namespace",
        comment="",
    )
    db.add(ed)
    await db.commit()
    await db.refresh(ed)
    return ns.id, ed.id


@pytest_asyncio.fixture
async def ns_with_sco_ref(db, ns_with_enum) -> tuple[int, int, int]:
    """Create SCO with fields_json referencing the enum. Returns (ns_id, enum_id, sco_id)."""
    ns_id, enum_id = ns_with_enum
    sco = SchemaCanonicalObject(
        namespace_id=ns_id,
        db_type="mongodb",
        database="db_test",
        target="orders",
        fields_json=json.dumps([
            {"name": "orderStatus", "type": "Integer", "enum_ref_id": enum_id,
             "enum_match_status": "matched"},
            {"name": "amount", "type": "Double"},
        ]),
    )
    db.add(sco)
    await db.commit()
    await db.refresh(sco)
    return ns_id, enum_id, sco.id


# ════════════════════════════════════════════
#  CREATE
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_create_enum_dictionary_201(admin_client, db):
    # setup user + ns
    existing = await db.get(User, 1)
    if not existing:
        db.add(User(id=1, username="admin", role="admin", password_hash="x"))
        await db.commit()

    ns = Namespace(name="create_test", slug="create_test", description="")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    resp = await admin_client.post("/api/enum-dictionary", json={
        "namespace_id": ns.id,
        "enum_class_name": "DeleteStatus",
        "values": [
            {"name": "NORMAL", "db_value": 0, "description": "正常"},
            {"name": "DELETED", "db_value": 1, "description": "已删除"},
        ],
        "scope": "namespace",
        "comment": "逻辑删除标记",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "manual"
    assert body["id"] > 0


@pytest.mark.asyncio
async def test_create_enum_dictionary_409_duplicate(admin_client, ns_with_enum):
    ns_id, _ = ns_with_enum
    resp = await admin_client.post("/api/enum-dictionary", json={
        "namespace_id": ns_id,
        "enum_class_name": "OrderStatus",  # already exists
        "values": [{"name": "X", "db_value": 0}],
    })
    assert resp.status_code == 409
    assert "已存在" in resp.json()["detail"]


# ════════════════════════════════════════════
#  LIST
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_list_enum_dictionaries_filter(admin_client, ns_with_sco_ref):
    ns_id, enum_id, _ = ns_with_sco_ref

    # list all
    resp = await admin_client.get(f"/api/enum-dictionary?namespace_id={ns_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    item = body["items"][0]
    assert item["enum_class_name"] == "OrderStatus"
    assert item["reference_count"] == 1  # one field references it

    # filter by source=manual → empty
    resp2 = await admin_client.get(
        f"/api/enum-dictionary?namespace_id={ns_id}&source=manual"
    )
    assert resp2.json()["total"] == 0

    # filter by name_like
    resp3 = await admin_client.get(
        f"/api/enum-dictionary?namespace_id={ns_id}&name_like=Order"
    )
    assert resp3.json()["total"] == 1


# ════════════════════════════════════════════
#  UPDATE
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_update_auto_converts_code_to_manual(admin_client, ns_with_enum, db):
    ns_id, enum_id = ns_with_enum
    resp = await admin_client.put(f"/api/enum-dictionary/{enum_id}", json={
        "values": [
            {"name": "CREATED", "db_value": 1, "description": "已创建"},
            {"name": "PAID", "db_value": 2, "description": "已支付"},
            {"name": "SHIPPED", "db_value": 3, "description": "已发货"},
        ],
        "comment": "added shipped",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "manual"
    assert len(body["values"]) == 3


# ════════════════════════════════════════════
#  DELETE
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delete_dry_run(admin_client, ns_with_sco_ref):
    ns_id, enum_id, _ = ns_with_sco_ref
    resp = await admin_client.delete(
        f"/api/enum-dictionary/{enum_id}?dry_run=true"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["affected_fields"]) == 1
    assert body["affected_fields"][0]["field"] == "orderStatus"


@pytest.mark.asyncio
async def test_delete_confirm(admin_client, ns_with_sco_ref, db):
    ns_id, enum_id, _ = ns_with_sco_ref
    resp = await admin_client.delete(
        f"/api/enum-dictionary/{enum_id}?dry_run=false"
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # verify deleted
    db.expire_all()
    row = await db.get(EnumDictionary, enum_id)
    assert row is None


# ════════════════════════════════════════════
#  REFERENCES
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_references_endpoint(admin_client, ns_with_sco_ref):
    ns_id, enum_id, sco_id = ns_with_sco_ref
    resp = await admin_client.get(f"/api/enum-dictionary/{enum_id}/references")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    ref = body["items"][0]
    assert ref["collection_id"] == sco_id
    assert ref["field"] == "orderStatus"
    assert ref["enum_match_status"] == "matched"
