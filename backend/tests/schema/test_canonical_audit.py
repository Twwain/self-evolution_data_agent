"""Phase 1 Task 3: SchemaCanonicalAuditLog helper 测试."""
import json

import pytest
from sqlalchemy import select

from app.knowledge.canonical_audit import write_canonical_audit_log
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
    log_id = await write_canonical_audit_log(
        test_session,
        namespace_id=ns.id,
        action="auto_promote",
        candidate_id=42,
        conflict_id=None,
        canonical_id=99,
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
    assert row.candidate_id == 42
    assert row.canonical_id == 99
