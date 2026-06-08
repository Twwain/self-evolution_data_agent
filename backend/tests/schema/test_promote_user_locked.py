"""user_locked=True 字段 promote 跳过保护."""
import json

import pytest
from sqlalchemy import select

from app.knowledge.canonical_candidate import write_canonical_candidate
from app.knowledge.canonical_promote import promote_candidates_to_canonical
from app.models import SchemaCanonicalAuditLog, SchemaCanonicalObject

pytestmark = pytest.mark.asyncio


async def test_user_locked_skipped(test_session, namespace_factory):
    """SchemaCanonicalObject.user_locked=True 时 promote 跳过."""
    ns = await namespace_factory()
    # 先建一个已 user_locked 的 SchemaCanonicalObject
    sco = SchemaCanonicalObject(
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        fields_json='[{"name":"status","description":"用户编辑值"}]',
        indexes_json="[]", description="", purpose_detail="",
        sample_count=0, source="manual",
        relationships_json="[]", sample_values_json="[]", user_locked=True,
    )
    test_session.add(sco)
    await test_session.commit()

    # 候选试图覆盖
    await write_canonical_candidate(
        test_session,
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        field_path="status", candidate_kind="field_description",
        candidate_value={"description": "自动抽到的值"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.skipped_user_locked == 1

    # SCO description 仍是用户值
    sco_after = await test_session.get(SchemaCanonicalObject, sco.id)
    fields = json.loads(sco_after.fields_json)
    assert fields[0]["description"] == "用户编辑值"

    # 留 audit
    audits = (await test_session.execute(
        select(SchemaCanonicalAuditLog).where(
            SchemaCanonicalAuditLog.namespace_id == ns.id,
            SchemaCanonicalAuditLog.action == "skipped_user_locked",
        )
    )).scalars().all()
    assert len(audits) == 1
