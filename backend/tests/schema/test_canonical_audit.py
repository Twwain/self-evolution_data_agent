"""Phase 1 Task 3: SchemaCanonicalAuditLog helper 测试."""
import json

import pytest
from sqlalchemy import select

from app.knowledge.canonical_audit import write_canonical_audit_log
from app.knowledge.canonical_candidate import write_canonical_candidate
from app.knowledge.schema_canonical import upsert_schema_canonical
from app.models import SchemaCanonicalAuditLog

pytestmark = pytest.mark.asyncio


async def test_write_audit_log_minimal(test_session, namespace_factory):
    ns = await namespace_factory()
    log_id = await write_canonical_audit_log(
        test_session,
        namespace_id=ns.id,
        action="auto_extract",
        candidate_id=None,
        conflict_id=None,
        canonical_id=None,
        field_path=None,
        before=None,
        after=None,
        reason=None,
        actor_id=None,
        extra=None,
    )
    await test_session.commit()

    row = (await test_session.execute(
        select(SchemaCanonicalAuditLog).where(SchemaCanonicalAuditLog.id == log_id)
    )).scalar_one()
    assert row.namespace_id == ns.id
    assert row.action == "auto_extract"
    assert row.actor_id is None  # 系统行为


async def test_write_audit_log_with_diff(test_session, namespace_factory):
    ns = await namespace_factory()
    # 建真实 candidate + SCO, 用其真实 id (audit_log 对二者有外键约束)
    cand_id = await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="test_db",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "状态"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    sco = await upsert_schema_canonical(
        test_session, namespace_id=ns.id, db_type="mysql",
        database="test_db", target="t_order",
    )
    await test_session.commit()

    log_id = await write_canonical_audit_log(
        test_session,
        namespace_id=ns.id,
        action="auto_promote",
        candidate_id=cand_id,
        conflict_id=None,
        canonical_id=sco.id,
        field_path="t_order.status",
        before={"description": "状态"},
        after={"description": "订单状态: 1=已支付..."},
        reason="single_source_authoritative",
        actor_id=None,
        extra={"source": "introspect"},
    )
    await test_session.commit()

    row = (await test_session.execute(
        select(SchemaCanonicalAuditLog).where(SchemaCanonicalAuditLog.id == log_id)
    )).scalar_one()
    assert row.field_path == "t_order.status"
    assert json.loads(row.before_json) == {"description": "状态"}
    assert json.loads(row.after_json)["description"].startswith("订单状态")
    assert row.reason == "single_source_authoritative"
    assert row.candidate_id == cand_id
    assert row.canonical_id == sco.id
