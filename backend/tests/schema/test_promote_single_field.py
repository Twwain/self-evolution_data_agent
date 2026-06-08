"""promote_single_field 单字段 promote (T5/T6 用)."""
import pytest
from sqlalchemy import select

from app.knowledge.canonical_candidate import write_canonical_candidate
from app.knowledge.canonical_promote import promote_single_field
from app.models import SchemaCanonicalCandidate, SchemaCanonicalObject

pytestmark = pytest.mark.asyncio


async def test_promote_single_field_works(test_session, namespace_factory):
    """单字段 promote 正常路径."""
    ns = await namespace_factory()
    await write_canonical_candidate(
        test_session,
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        field_path="status", candidate_kind="field_description",
        candidate_value={"description": "订单状态"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    report = await promote_single_field(
        test_session, ns_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
    )
    await test_session.commit()
    assert report.promoted_count == 1

    # candidate 标 active
    cand = (await test_session.execute(
        select(SchemaCanonicalCandidate).where(SchemaCanonicalCandidate.namespace_id == ns.id)
    )).scalar_one()
    assert cand.status == "active"

    # SCO 已写
    sco = (await test_session.execute(
        select(SchemaCanonicalObject).where(SchemaCanonicalObject.namespace_id == ns.id)
    )).scalar_one()
    assert sco is not None


async def test_promote_single_field_no_pending(test_session, namespace_factory):
    """无 pending 候选时返回 0."""
    ns = await namespace_factory()
    report = await promote_single_field(
        test_session, ns_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
    )
    assert report.promoted_count == 0
    assert report.candidates_processed == 0
