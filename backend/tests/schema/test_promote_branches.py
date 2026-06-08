"""Phase 1 Task 4: promote_candidates_to_canonical 9 分支判断."""
import json

import pytest
from sqlalchemy import select

from app.knowledge.canonical_candidate import write_canonical_candidate
from app.knowledge.canonical_promote import promote_candidates_to_canonical
from app.models import (
    SchemaCanonicalAuditLog,
    SchemaCanonicalCandidate,
    SchemaCanonicalConflict,
    SchemaCanonicalObject,
)

pytestmark = pytest.mark.asyncio


# ────────────────────────────────────────────────────────────
# N = 0 分支
# ────────────────────────────────────────────────────────────


async def test_n0_no_candidates_no_change(test_session, namespace_factory):
    """无 pending 候选时 promote 返回 0, 不报错."""
    ns = await namespace_factory()
    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.promoted_count == 0
    assert report.candidates_processed == 0


# ────────────────────────────────────────────────────────────
# N = 1 分支
# ────────────────────────────────────────────────────────────


async def test_n1_introspect_auto_promotes(test_session, namespace_factory):
    """N=1 + confirmed_by_introspect → AUTO PROMOTE."""
    ns = await namespace_factory()
    await write_canonical_candidate(
        test_session,
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        field_path="status", candidate_kind="field_description",
        candidate_value={"description": "订单状态"},
        evidence_sources=[{"source": "introspect", "datasource_id": 1}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.promoted_count == 1
    assert report.conflicted_count == 0

    # SchemaCanonicalObject 已落
    sco = (await test_session.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == ns.id,
            SchemaCanonicalObject.target == "t_order",
        )
    )).scalar_one()
    fields = json.loads(sco.fields_json)
    assert any(f["name"] == "status" and f["description"] == "订单状态" for f in fields)

    # candidate 标 active
    cand = (await test_session.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns.id,
            SchemaCanonicalCandidate.field_path == "status",
        )
    )).scalar_one()
    assert cand.status == "active"
    assert cand.promoted_at is not None

    # audit_log 留痕
    audits = (await test_session.execute(
        select(SchemaCanonicalAuditLog).where(
            SchemaCanonicalAuditLog.namespace_id == ns.id,
            SchemaCanonicalAuditLog.action == "auto_promote",
        )
    )).scalars().all()
    assert len(audits) >= 1
    assert audits[0].reason == "single_source_authoritative"


async def test_n1_evidence_only_stays_pending(test_session, namespace_factory):
    """仅 evidence_only 时, 不自动 promote, 保 pending."""
    ns = await namespace_factory()
    await write_canonical_candidate(
        test_session,
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        field_path="status", candidate_kind="field_description",
        candidate_value={"description": "推断: WHERE 高频 status=2"},
        evidence_sources=[{"source": "mybatis_where_literal", "mapper": "OrderMapper"}],
        confidence_status="evidence_only",
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.promoted_count == 0

    cand = (await test_session.execute(
        select(SchemaCanonicalCandidate).where(SchemaCanonicalCandidate.namespace_id == ns.id)
    )).scalar_one()
    assert cand.status == "pending"


# ────────────────────────────────────────────────────────────
# N ≥ 2 分支
# ────────────────────────────────────────────────────────────


async def test_n2_same_hash_auto_promotes_merges_evidence(test_session, namespace_factory):
    """两 repo 给字面相同 description → dedup 合并 → N=1 auto promote."""
    ns = await namespace_factory()
    id1 = await write_canonical_candidate(
        test_session,
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        field_path="status", candidate_kind="field_description",
        candidate_value={"description": "订单状态"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    id2 = await write_canonical_candidate(
        test_session,
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        field_path="status", candidate_kind="field_description",
        candidate_value={"description": "订单状态"},  # 相同值 → dedup
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": 7}],
        confidence_status="confirmed_by_code",
    )
    assert id1 == id2  # dedup 复用同行
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.promoted_count == 1


async def test_n2_different_hash_creates_conflict(test_session, namespace_factory):
    """两候选字面不同 → 写 Conflict."""
    ns = await namespace_factory()
    await write_canonical_candidate(
        test_session,
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        field_path="status", candidate_kind="field_description",
        candidate_value={"description": "订单状态"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await write_canonical_candidate(
        test_session,
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        field_path="status", candidate_kind="field_description",
        candidate_value={"description": "订单状态: 1=已支付,2=已发货,3=完成"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": 7}],
        confidence_status="confirmed_by_code",
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.promoted_count == 0
    assert report.conflicted_count == 1

    # Conflict 已写
    conflict = (await test_session.execute(
        select(SchemaCanonicalConflict).where(SchemaCanonicalConflict.namespace_id == ns.id)
    )).scalar_one()
    assert conflict.status == "open"
    assert conflict.conflict_type == "field_value"
    assert conflict.field_path == "status"

    # 两 candidate 都标 in_conflict
    cands = (await test_session.execute(
        select(SchemaCanonicalCandidate).where(SchemaCanonicalCandidate.namespace_id == ns.id)
    )).scalars().all()
    assert {c.status for c in cands} == {"in_conflict"}


async def test_description_one_empty_one_filled_picks_filled(test_session, namespace_factory):
    """description 一空一非空 → 取非空, AUTO PROMOTE (non_empty_wins)."""
    ns = await namespace_factory()
    await write_canonical_candidate(
        test_session,
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        field_path="status", candidate_kind="field_description",
        candidate_value={"description": ""},  # 空
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await write_canonical_candidate(
        test_session,
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        field_path="status", candidate_kind="field_description",
        candidate_value={"description": "订单状态"},  # 非空
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="confirmed_by_code",
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.promoted_count == 1

    sco = (await test_session.execute(
        select(SchemaCanonicalObject).where(SchemaCanonicalObject.namespace_id == ns.id)
    )).scalar_one()
    fields = json.loads(sco.fields_json)
    assert any(f["description"] == "订单状态" for f in fields if f["name"] == "status")


async def test_promote_report_counts_correct(test_session, namespace_factory):
    """幂等: 重跑 promote 没有 pending 时返回 0."""
    ns = await namespace_factory()
    report1 = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report1.promoted_count == 0

    await write_canonical_candidate(
        test_session,
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        field_path="status", candidate_kind="field_description",
        candidate_value={"description": "订单状态"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    r1 = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    r2 = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert r1.promoted_count == 1
    assert r2.promoted_count == 0  # 第二次幂等
