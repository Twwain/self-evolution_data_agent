"""Phase 1 Task 11: 两 repo 并发训练完成时, race 不写错值.

验证多 repo 同时写候选 + 触发 promote 时:
1. 不同值 → conflict (不写错值到 canonical)
2. 相同值 → auto promote (dedup)
3. 并发 promote 幂等
"""
import asyncio

import pytest
from sqlalchemy import select

from app.knowledge.canonical_candidate import write_canonical_candidate
from app.knowledge.canonical_promote import promote_candidates_to_canonical
from app.models import (
    SchemaCanonicalConflict,
    SchemaCanonicalObject,
)

pytestmark = pytest.mark.asyncio


async def test_two_repos_same_field_different_value_creates_conflict(
    test_session, namespace_factory, repo_factory,
):
    """两 repo 写不同值 → promote 产 conflict, canonical 不被错值污染."""
    ns = await namespace_factory()
    repo_a = await repo_factory(ns_id=ns.id)
    repo_b = await repo_factory(ns_id=ns.id)

    # 两 repo 各写一条不同值候选
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "V_A"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo_a.id}],
        confidence_status="confirmed_by_code", repo_id=repo_a.id,
    )
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "V_B"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo_b.id}],
        confidence_status="confirmed_by_code", repo_id=repo_b.id,
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()

    # 应有 conflict
    assert report.conflicted_count == 1
    assert report.promoted_count == 0

    conflicts = (await test_session.execute(
        select(SchemaCanonicalConflict).where(
            SchemaCanonicalConflict.namespace_id == ns.id,
            SchemaCanonicalConflict.status == "open",
        )
    )).scalars().all()
    assert len(conflicts) == 1

    # SCO 不应被创建 (conflict 拦截, 不写错值)
    sco_rows = (await test_session.execute(
        select(SchemaCanonicalObject).where(SchemaCanonicalObject.namespace_id == ns.id)
    )).scalars().all()
    assert len(sco_rows) == 0


async def test_two_repos_same_field_same_value_auto_promotes(
    test_session, namespace_factory, repo_factory,
):
    """两 repo 写相同值 → dedup 合并 evidence → auto promote."""
    ns = await namespace_factory()
    repo_a = await repo_factory(ns_id=ns.id)
    repo_b = await repo_factory(ns_id=ns.id)

    # 两 repo 写相同值 (dedup 会合并到同一行)
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "一致的描述"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo_a.id}],
        confidence_status="confirmed_by_code", repo_id=repo_a.id,
    )
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "一致的描述"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo_b.id}],
        confidence_status="confirmed_by_code", repo_id=repo_b.id,
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()

    # dedup 后 N=1, auto promote
    assert report.promoted_count == 1
    assert report.conflicted_count == 0

    sco = (await test_session.execute(
        select(SchemaCanonicalObject).where(SchemaCanonicalObject.namespace_id == ns.id)
    )).scalar_one()
    assert "一致的描述" in sco.fields_json


async def test_concurrent_promote_idempotent(
    test_session, namespace_factory,
):
    """asyncio.gather 两个 promote, 结果一致且幂等."""
    ns = await namespace_factory()
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "concurrent_test"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    r1, r2 = await asyncio.gather(
        promote_candidates_to_canonical(test_session, ns.id),
        promote_candidates_to_canonical(test_session, ns.id),
    )
    await test_session.commit()

    # 总共仅 promote 1 次
    total_promoted = r1.promoted_count + r2.promoted_count
    assert total_promoted == 1

    # SCO 值正确
    sco = (await test_session.execute(
        select(SchemaCanonicalObject).where(SchemaCanonicalObject.namespace_id == ns.id)
    )).scalar_one()
    assert "concurrent_test" in sco.fields_json
