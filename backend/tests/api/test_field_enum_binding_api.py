"""Phase 2 Plan 04 Task 2-4 — field bind/unbind/inspect_samples/pending_list.

覆盖:
- bind matched / bind sample 不覆盖 422 / force=true 写 conflict / unbind 字段名含后缀回 pending
- inspect_samples mock
- pending_enum_binding list
"""
import json

import pytest
import pytest_asyncio

from app.models.enum_dictionary import EnumDictionary
from app.models.namespace import Namespace
from app.models.schema_canonical_object import SchemaCanonicalObject
from app.models.user import User


@pytest_asyncio.fixture
async def binding_setup(db) -> dict:
    """Create ns + enum + SCO with fields for binding tests.

    Returns dict with ns_id, enum_id, sco_id, collection_id.
    """
    existing = await db.get(User, 1)
    if not existing:
        db.add(User(id=1, username="admin", role="admin", password_hash="x"))
        await db.commit()

    ns = Namespace(name="bind_test", slug="bind_test", description="")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    ed = EnumDictionary(
        namespace_id=ns.id,
        enum_class_name="DeleteStatus",
        values_json=json.dumps([
            {"name": "NORMAL", "db_value": 0, "description": "正常"},
            {"name": "DELETED", "db_value": 1, "description": "已删除"},
        ]),
        source="manual",
        scope="namespace",
        comment="",
    )
    db.add(ed)
    await db.commit()
    await db.refresh(ed)

    sco = SchemaCanonicalObject(
        namespace_id=ns.id,
        db_type="mongodb",
        database="db_test",
        target="users",
        fields_json=json.dumps([
            {"name": "isDeleted", "type": "Integer",
             "enum_match_status": "pending",
             "sample_values": [0, 1]},
            {"name": "userName", "type": "String"},
            {"name": "orderStatus", "type": "Integer",
             "enum_match_status": "pending",
             "sample_values": [0, 1, 2, 3]},  # won't cover DeleteStatus
        ]),
    )
    db.add(sco)
    await db.commit()
    await db.refresh(sco)

    return {
        "ns_id": ns.id,
        "enum_id": ed.id,
        "sco_id": sco.id,
    }


# ════════════════════════════════════════════
#  BIND
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bind_enum_matched(admin_client, binding_setup, db):
    """sample_values [0,1] fully covered by enum {0,1} → matched."""
    s = binding_setup
    resp = await admin_client.post(
        f"/api/namespaces/{s['ns_id']}/schema-canonical"
        f"/{s['sco_id']}/fields/isDeleted/bind_enum",
        json={"enum_dict_id": s["enum_id"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enum_match_status"] == "matched"
    assert body["enum_ref_id"] == s["enum_id"]

    # verify fields_json persisted
    db.expire_all()
    sco = await db.get(SchemaCanonicalObject, s["sco_id"])
    fields = json.loads(sco.fields_json)
    f = next(f for f in fields if f["name"] == "isDeleted")
    assert f["enum_ref_id"] == s["enum_id"]
    assert f["enum_source"] == "manual_binding"


@pytest.mark.asyncio
async def test_bind_enum_sample_not_covered_422(admin_client, binding_setup):
    """sample_values [0,1,2,3] not covered by enum {0,1} → 422."""
    s = binding_setup
    resp = await admin_client.post(
        f"/api/namespaces/{s['ns_id']}/schema-canonical"
        f"/{s['sco_id']}/fields/orderStatus/bind_enum",
        json={"enum_dict_id": s["enum_id"], "force": False},
    )
    assert resp.status_code == 422
    assert "force" in resp.json()["detail"].lower() or "force" in resp.text.lower()


@pytest.mark.asyncio
async def test_bind_enum_force_conflict(admin_client, binding_setup, db):
    """force=true with uncovered samples → conflict status."""
    s = binding_setup
    resp = await admin_client.post(
        f"/api/namespaces/{s['ns_id']}/schema-canonical"
        f"/{s['sco_id']}/fields/orderStatus/bind_enum",
        json={"enum_dict_id": s["enum_id"], "force": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enum_match_status"] == "conflict"
    assert body["enum_ref_id"] == s["enum_id"]


# ════════════════════════════════════════════
#  UNBIND
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_unbind_field_with_enum_suffix_reverts_to_pending(
    admin_client, binding_setup, db,
):
    """Unbind a field whose name has enum suffix → enum_match_status='pending'."""
    s = binding_setup
    # First bind
    await admin_client.post(
        f"/api/namespaces/{s['ns_id']}/schema-canonical"
        f"/{s['sco_id']}/fields/orderStatus/bind_enum",
        json={"enum_dict_id": s["enum_id"], "force": True},
    )
    # Then unbind
    resp = await admin_client.delete(
        f"/api/namespaces/{s['ns_id']}/schema-canonical"
        f"/{s['sco_id']}/fields/orderStatus/bind_enum",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enum_match_status"] == "pending"

    # verify fields_json
    db.expire_all()
    sco = await db.get(SchemaCanonicalObject, s["sco_id"])
    fields = json.loads(sco.fields_json)
    f = next(f for f in fields if f["name"] == "orderStatus")
    assert "enum_ref_id" not in f
    assert f["enum_match_status"] == "pending"


@pytest.mark.asyncio
async def test_unbind_field_without_suffix_removes_status(
    admin_client, binding_setup, db,
):
    """Unbind a field without enum suffix → enum_match_status removed."""
    s = binding_setup
    # First bind isDeleted (has suffix "Status" via "Deleted" — actually "isDeleted"
    # doesn't have a suffix in ENUM_NAME_SUFFIXES. Let's bind userName which has no suffix)
    # Actually let's bind isDeleted first then unbind
    await admin_client.post(
        f"/api/namespaces/{s['ns_id']}/schema-canonical"
        f"/{s['sco_id']}/fields/isDeleted/bind_enum",
        json={"enum_dict_id": s["enum_id"]},
    )
    # unbind — "isDeleted" splits to ["is", "Deleted"] — "Deleted" not in ENUM_NAME_SUFFIXES
    resp = await admin_client.delete(
        f"/api/namespaces/{s['ns_id']}/schema-canonical"
        f"/{s['sco_id']}/fields/isDeleted/bind_enum",
    )
    assert resp.status_code == 200
    body = resp.json()
    # "isDeleted" → tokens ["is", "Deleted"], "Deleted" not in ENUM_NAME_SUFFIXES
    # so enum_match_status should be removed (None)
    assert body.get("enum_match_status") is None


# ════════════════════════════════════════════
#  INSPECT SAMPLES (Task 3)
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_inspect_samples(admin_client, binding_setup, db, monkeypatch):
    """Mock inspect_field_values and verify sample_values written to fields_json."""
    s = binding_setup

    async def _mock_inspect(**kwargs):
        return {
            "collection": "users",
            "field": "isDeleted",
            "values": [0, 1],
            "truncated": False,
            "sample_requested": 50,
        }

    monkeypatch.setattr(
        "app.api.schema_canonical_v2.inspect_field_values", _mock_inspect
    )

    resp = await admin_client.post(
        f"/api/namespaces/{s['ns_id']}/schema-canonical"
        f"/{s['sco_id']}/fields/isDeleted/inspect_samples",
        json={"limit": 50},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sample_values"] == [0, 1]
    assert body["distinct_count"] == 2
    assert body["is_complete"] is True

    # verify persisted
    db.expire_all()
    sco = await db.get(SchemaCanonicalObject, s["sco_id"])
    fields = json.loads(sco.fields_json)
    f = next(f for f in fields if f["name"] == "isDeleted")
    assert f["sample_values"] == [0, 1]
    assert f["sample_metadata"]["distinct_count"] == 2


# ════════════════════════════════════════════
#  PENDING ENUM BINDING (Task 4)
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pending_enum_binding_list(admin_client, binding_setup):
    """SCO has 2 pending fields (isDeleted, orderStatus) → returns them."""
    s = binding_setup
    resp = await admin_client.get(
        f"/api/namespaces/{s['ns_id']}/schema-canonical"
        f"/fields/pending_enum_binding?namespace_id={s['ns_id']}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    names = {item["field"] for item in body["items"]}
    assert "isDeleted" in names
    assert "orderStatus" in names


@pytest.mark.asyncio
async def test_pending_enum_binding_empty(admin_client, db):
    """No pending fields → empty list."""
    existing = await db.get(User, 1)
    if not existing:
        db.add(User(id=1, username="admin", role="admin", password_hash="x"))
        await db.commit()

    ns = Namespace(name="empty_ns", slug="empty_ns", description="")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    sco = SchemaCanonicalObject(
        namespace_id=ns.id,
        db_type="mongodb",
        database="db_test",
        target="products",
        fields_json=json.dumps([
            {"name": "price", "type": "Double"},
        ]),
    )
    db.add(sco)
    await db.commit()

    resp = await admin_client.get(
        f"/api/namespaces/{ns.id}/schema-canonical"
        f"/fields/pending_enum_binding?namespace_id={ns.id}"
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
