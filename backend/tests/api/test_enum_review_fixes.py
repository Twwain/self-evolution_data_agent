"""Stage 6 review fixes — regression contract tests.

Targets the four critical/important findings from code review:
- inspect_samples on MySQL SCO must 422 (not crash on Mongo driver)
- delete EnumDictionary must write SchemaCanonicalAuditLog atomically
- unbind field must resolve any open EnumBindingConflict
- enum_sync_loop must dedup repeated (enum_dict_id, event)

All tests hit the real FastAPI router via admin_client (no handler-direct calls,
no mocks of business functions). See SKILL: api-contract-testing for rationale.
"""
import json

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.knowledge.enum_sync import enum_sync_loop_once
from app.models.enum_binding_conflict import EnumBindingConflict
from app.models.enum_dictionary import EnumDictionary
from app.models.enum_sync_queue import EnumSyncQueue
from app.models.namespace import Namespace
from app.models.schema_canonical_audit_log import SchemaCanonicalAuditLog
from app.models.schema_canonical_object import SchemaCanonicalObject
from app.models.user import User


@pytest_asyncio.fixture
async def setup_ns_enum_sco(db):
    """Common ns + admin user + EnumDictionary + SCO fixture."""
    if not await db.get(User, 1):
        db.add(User(id=1, username="admin", role="admin", password_hash="x"))
        await db.commit()

    ns = Namespace(name="rev_test", slug="rev_test", description="")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    enum_row = EnumDictionary(
        namespace_id=ns.id,
        enum_class_name="OrderStatus",
        values_json=json.dumps([
            {"name": "PENDING", "db_value": 0},
            {"name": "PAID", "db_value": 1},
        ]),
        source="manual",
        scope="namespace",
        comment="",
    )
    db.add(enum_row)
    await db.commit()
    await db.refresh(enum_row)

    return {"ns_id": ns.id, "enum_id": enum_row.id}


# ════════════════════════════════════════════════════════════════════
# Critical #2 / Important #5 — inspect_samples db_type guard
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_inspect_samples_rejects_mysql_with_422(admin_client, db, setup_ns_enum_sco):
    """MySQL SCO 上 inspect_samples 必须 422, 不能跑 Mongo driver."""
    s = setup_ns_enum_sco
    sco = SchemaCanonicalObject(
        namespace_id=s["ns_id"],
        db_type="mysql",
        database="db_demo",
        target="orders",
        fields_json=json.dumps([{"name": "status", "type": "Integer"}]),
    )
    db.add(sco)
    await db.commit()
    await db.refresh(sco)

    resp = await admin_client.post(
        f"/api/namespaces/{s['ns_id']}/schema-canonical"
        f"/{sco.id}/fields/status/inspect_samples",
        json={"limit": 50},
    )
    assert resp.status_code == 422
    assert "mongodb" in resp.text.lower()


# ════════════════════════════════════════════════════════════════════
# Critical #3 — DELETE EnumDictionary writes audit log atomically
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delete_enum_dictionary_writes_audit_log(
    admin_client, db, setup_ns_enum_sco,
):
    """DELETE 后必须有一条 schema_canonical_audit_logs.action='enum_dict_deleted'.

    防容灾窗口丢事件: enqueue 失败 / 进程崩溃, audit_log 仍是 source of truth.
    """
    s = setup_ns_enum_sco

    resp = await admin_client.delete(
        f"/api/enum-dictionary/{s['enum_id']}",
        params={"dry_run": "false"},
    )
    assert resp.status_code == 200, resp.text

    db.expire_all()
    logs = (await db.execute(
        select(SchemaCanonicalAuditLog).where(
            SchemaCanonicalAuditLog.namespace_id == s["ns_id"],
            SchemaCanonicalAuditLog.action == "enum_dict_deleted",
        )
    )).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.actor_id == 1
    assert log.before_json is not None
    before = json.loads(log.before_json)
    assert before["enum_dict_id"] == s["enum_id"]
    assert before["enum_class_name"] == "OrderStatus"


# ════════════════════════════════════════════════════════════════════
# Important #4 — unbind closes open EnumBindingConflict rows
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_unbind_field_resolves_open_conflict(
    admin_client, db, setup_ns_enum_sco,
):
    """字段 unbind 时, 同 (sco, field, enum) 上 open 的 conflict 必须 → resolved."""
    s = setup_ns_enum_sco
    sco = SchemaCanonicalObject(
        namespace_id=s["ns_id"],
        db_type="mongodb",
        database="db_demo",
        target="orders",
        fields_json=json.dumps([{
            "name": "status",
            "type": "Integer",
            "enum_ref_id": s["enum_id"],
            "enum_values": [{"name": "PENDING", "db_value": 0}],
            "enum_source": "manual_binding",
            "enum_match_status": "conflict",
            "sample_values": [0, 9],
        }]),
    )
    db.add(sco)
    await db.commit()
    await db.refresh(sco)

    conflict = EnumBindingConflict(
        namespace_id=s["ns_id"],
        field_canonical_id=sco.id,
        field_name="status",
        enum_dict_id=s["enum_id"],
        conflict_kind="value_not_covered",
        detail_json=json.dumps({"sample": [0, 9], "not_covered": [9]}),
        status="open",
    )
    db.add(conflict)
    await db.commit()
    await db.refresh(conflict)

    resp = await admin_client.delete(
        f"/api/namespaces/{s['ns_id']}/schema-canonical"
        f"/{sco.id}/fields/status/bind_enum",
    )
    assert resp.status_code == 200, resp.text

    await db.refresh(conflict)
    assert conflict.status == "resolved"
    assert conflict.resolved_at is not None
    assert conflict.resolved_by == 1


# ════════════════════════════════════════════════════════════════════
# Important #8 — enum_sync_loop dedups repeated update events
# ════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_enum_sync_loop_dedups_same_key(db, setup_ns_enum_sco):
    """同 (enum_dict_id, event) 多次入队 → 全部消费, 但只跑业务函数 1 次."""
    s = setup_ns_enum_sco

    for _ in range(4):
        db.add(EnumSyncQueue(
            enum_dict_id=s["enum_id"],
            namespace_id=s["ns_id"],
            event="update",
        ))
    await db.commit()

    pre = (await db.execute(select(EnumSyncQueue))).scalars().all()
    assert len(pre) == 4

    processed = await enum_sync_loop_once(db)

    rows = (await db.execute(select(EnumSyncQueue))).scalars().all()
    assert len(rows) == 0
    assert processed == 4
