"""Phase 1 Task 3: write_canonical_candidate UPSERT + dedup 测试."""
import json

import pytest
from sqlalchemy import select

from app.knowledge.canonical_candidate import write_canonical_candidate
from app.models import SchemaCanonicalAuditLog, SchemaCanonicalCandidate

pytestmark = pytest.mark.asyncio


async def test_first_write_creates_candidate(test_session, namespace_factory):
    ns = await namespace_factory()
    cand_id = await write_canonical_candidate(
        test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
        field_path="status",
        candidate_kind="field_description",
        candidate_value={"description": "订单状态"},
        evidence_sources=[{"source": "introspect", "datasource_id": 1}],
        confidence_status="confirmed_by_introspect",
        repo_id=None,
        datasource_id=1,
    )
    await test_session.commit()

    row = (await test_session.execute(
        select(SchemaCanonicalCandidate).where(SchemaCanonicalCandidate.id == cand_id)
    )).scalar_one()
    assert row.status == "pending"
    assert row.confidence_status == "confirmed_by_introspect"
    assert json.loads(row.candidate_value_json)["description"] == "订单状态"
    assert len(json.loads(row.evidence_sources_json)) == 1

    # audit_log 留痕
    audit_rows = (await test_session.execute(
        select(SchemaCanonicalAuditLog).where(
            SchemaCanonicalAuditLog.candidate_id == cand_id,
            SchemaCanonicalAuditLog.action == "auto_extract",
        )
    )).scalars().all()
    assert len(audit_rows) == 1


async def test_second_write_same_value_dedups_and_merges_evidence(test_session, namespace_factory):
    ns = await namespace_factory()
    # 第一次 introspect 来源
    id1 = await write_canonical_candidate(
        test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
        field_path="status",
        candidate_kind="field_description",
        candidate_value={"description": "订单状态"},
        evidence_sources=[{"source": "introspect", "datasource_id": 1}],
        confidence_status="confirmed_by_introspect",
        repo_id=None,
        datasource_id=1,
    )
    await test_session.commit()

    # 第二次 JPA Javadoc 来源, 同 value
    id2 = await write_canonical_candidate(
        test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
        field_path="status",
        candidate_kind="field_description",
        candidate_value={"description": "订单状态"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": 7, "file": "OrderEntity.java"}],
        confidence_status="confirmed_by_code",
        repo_id=7,
        datasource_id=None,
    )
    await test_session.commit()

    # 同一行复用
    assert id1 == id2

    row = await test_session.get(SchemaCanonicalCandidate, id1)
    sources = json.loads(row.evidence_sources_json)
    assert len(sources) == 2  # introspect + code_jpa_javadoc 都在
    src_kinds = {s["source"] for s in sources}
    assert "introspect" in src_kinds
    assert "code_jpa_javadoc" in src_kinds


async def test_different_value_creates_separate_candidate(test_session, namespace_factory):
    ns = await namespace_factory()
    id1 = await write_canonical_candidate(
        test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
        field_path="status",
        candidate_kind="field_description",
        candidate_value={"description": "订单状态"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    id2 = await write_canonical_candidate(
        test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
        field_path="status",
        candidate_kind="field_description",
        candidate_value={"description": "订单状态: 1=已支付..."},  # 不同值
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="confirmed_by_code",
    )
    await test_session.commit()
    assert id1 != id2


async def test_resurrect_superseded_candidate(test_session, namespace_factory):
    """旧 candidate.status=superseded 时, 同 value 再写应复活到 status=pending."""
    ns = await namespace_factory()
    id1 = await write_canonical_candidate(
        test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
        field_path="status",
        candidate_kind="field_description",
        candidate_value={"description": "订单状态"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    # 模拟被 supersede
    row = await test_session.get(SchemaCanonicalCandidate, id1)
    row.status = "superseded"
    await test_session.commit()

    # 重写
    id2 = await write_canonical_candidate(
        test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
        field_path="status",
        candidate_kind="field_description",
        candidate_value={"description": "订单状态"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    assert id1 == id2
    row = await test_session.get(SchemaCanonicalCandidate, id1)
    assert row.status == "pending"  # 复活


async def test_value_hash_deterministic(test_session, namespace_factory):
    """同 value 不同 key 顺序 → 同 hash → dedup 到同一行."""
    ns = await namespace_factory()
    id1 = await write_canonical_candidate(
        test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
        field_path="status",
        candidate_kind="field_description",
        candidate_value={"description": "订单状态", "extra": "info"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    # 同 value 但 key 顺序不同
    id2 = await write_canonical_candidate(
        test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_order",
        field_path="status",
        candidate_kind="field_description",
        candidate_value={"extra": "info", "description": "订单状态"},  # key 顺序不同
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="confirmed_by_code",
    )
    await test_session.commit()
    assert id1 == id2  # 同 hash → dedup


async def test_audit_log_written_on_insert(test_session, namespace_factory):
    """每次 INSERT 都有对应 auto_extract audit_log."""
    ns = await namespace_factory()
    cand_id = await write_canonical_candidate(
        test_session,
        namespace_id=ns.id,
        db_type="mysql",
        database="test_db",
        target="t_user",
        field_path="name",
        candidate_kind="field_description",
        candidate_value={"description": "用户名"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    logs = (await test_session.execute(
        select(SchemaCanonicalAuditLog).where(
            SchemaCanonicalAuditLog.candidate_id == cand_id,
        )
    )).scalars().all()
    assert len(logs) == 1
    assert logs[0].action == "auto_extract"
    assert logs[0].reason == "new_candidate"
    assert logs[0].namespace_id == ns.id
