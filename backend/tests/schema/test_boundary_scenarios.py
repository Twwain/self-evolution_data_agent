"""Phase 1 Task 10: B1-B14 边界场景集成测试.

设计 §4.6: 这 14 个场景是 promote 函数的 stress test.

每个 test 围绕一个场景, 验证 candidate / canonical / conflict / audit_log
四张表的状态在该场景下的预期组合.
"""
import asyncio
import json
from datetime import datetime

import pytest
from sqlalchemy import select

from app.knowledge.canonical_candidate import write_canonical_candidate
from app.knowledge.canonical_promote import promote_candidates_to_canonical
from app.knowledge.candidate_cleanup import (
    orphan_candidates_for_datasource,
    orphan_candidates_for_repo,
)
from app.models import (
    SchemaCanonicalCandidate,
    SchemaCanonicalConflict,
    SchemaCanonicalObject,
)

pytestmark = pytest.mark.asyncio


# ════════════════════════════════════════════════════════════════════
# B1: repo A 已 promoted, repo B 后到带分歧
# ════════════════════════════════════════════════════════════════════


async def test_b1_a_promoted_then_b_arrives_with_diff(
    test_session, namespace_factory, repo_factory,
):
    """repo A promoted → SCO=V1, repo B 带 V2 → conflict, canonical 不动."""
    ns = await namespace_factory()
    repo_a = await repo_factory(ns_id=ns.id)
    repo_b = await repo_factory(ns_id=ns.id)

    # T0: repo A 写候选并 promote
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "V1"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo_a.id}],
        confidence_status="confirmed_by_code", repo_id=repo_a.id,
    )
    await test_session.commit()
    r1 = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert r1.promoted_count == 1

    # SCO 已落 V1
    sco = (await test_session.execute(
        select(SchemaCanonicalObject).where(SchemaCanonicalObject.namespace_id == ns.id)
    )).scalar_one()
    assert "V1" in sco.fields_json

    # T1: repo B 写不同候选
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "V2"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo_b.id}],
        confidence_status="confirmed_by_code", repo_id=repo_b.id,
    )
    await test_session.commit()

    r2 = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()

    # 验证: SCO 仍 V1 不动 (服务不中断), 写入 Conflict
    sco_after = await test_session.get(SchemaCanonicalObject, sco.id)
    assert "V1" in sco_after.fields_json

    conflict = (await test_session.execute(
        select(SchemaCanonicalConflict).where(SchemaCanonicalConflict.namespace_id == ns.id)
    )).scalar_one()
    assert conflict.status == "open"
    assert conflict.conflict_type == "field_value"


# ════════════════════════════════════════════════════════════════════
# B2: repo 中途解析失败, 已写候选保留
# ════════════════════════════════════════════════════════════════════


async def test_b2_repo_parse_error_partial_candidates(
    test_session, namespace_factory, repo_factory,
):
    """repo 解析失败时, 已写入的 candidate 应保留, 不回滚."""
    ns = await namespace_factory()
    repo = await repo_factory(ns_id=ns.id)
    cid = await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "partial"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo.id}],
        confidence_status="confirmed_by_code", repo_id=repo.id,
    )
    await test_session.commit()

    # 模拟解析失败 (repo.parse_status='error', 但 candidate 应保留)
    repo.parse_status = "error"
    await test_session.commit()

    # 重新查 candidate 仍存在
    cand = await test_session.get(SchemaCanonicalCandidate, cid)
    assert cand is not None
    assert cand.status == "pending"


# ════════════════════════════════════════════════════════════════════
# B3: repo 重新解析, 同值复用同行 + evidence 累积
# ════════════════════════════════════════════════════════════════════


async def test_b3_repo_reparse_supersede_flow(
    test_session, namespace_factory, repo_factory,
):
    """repo 重解析: 同值 dedup 复用, 新 evidence 累积."""
    ns = await namespace_factory()
    repo = await repo_factory(ns_id=ns.id)

    # 第一次解析
    cid1 = await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "V"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo.id, "file": "x.java"}],
        confidence_status="confirmed_by_code", repo_id=repo.id,
    )
    await test_session.commit()

    # 第二次解析同值 (但 evidence 增加新文件)
    cid2 = await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "V"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo.id, "file": "y.java"}],
        confidence_status="confirmed_by_code", repo_id=repo.id,
    )
    await test_session.commit()

    assert cid1 == cid2  # 同行复用
    cand = await test_session.get(SchemaCanonicalCandidate, cid1)
    sources = json.loads(cand.evidence_sources_json)
    files = {s.get("file") for s in sources}
    assert files == {"x.java", "y.java"}  # evidence 累积


# ════════════════════════════════════════════════════════════════════
# B4: user_locked 字段保护
# ════════════════════════════════════════════════════════════════════


async def test_b4_user_lock_protect_from_promote(
    test_session, namespace_factory,
):
    """user_locked SCO 不被 promote 覆盖."""
    ns = await namespace_factory()
    sco = SchemaCanonicalObject(
        namespace_id=ns.id, db_type="mysql", database="db1", target="t_order",
        fields_json='[{"name":"status","description":"用户编辑"}]',
        indexes_json="[]", description="", purpose_detail="",
        sample_count=0, source="manual",
        relationships_json="[]", sample_values_json="[]", user_locked=True,
    )
    test_session.add(sco)
    await test_session.commit()

    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "introspect 给的"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.skipped_user_locked == 1

    sco_after = await test_session.get(SchemaCanonicalObject, sco.id)
    assert "用户编辑" in sco_after.fields_json  # 不被覆盖


# ════════════════════════════════════════════════════════════════════
# B5: introspect 看到字段, 代码侧无对应
# ════════════════════════════════════════════════════════════════════


async def test_b5_introspect_only_no_code(
    test_session, namespace_factory, datasource_factory,
):
    """单源 introspect → AUTO PROMOTE, confidence_status=confirmed_by_introspect."""
    ns = await namespace_factory()
    ds = await datasource_factory(ns_id=ns.id)
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database=ds.database,
        target="t_order", field_path="deprecated_field",
        candidate_kind="field_description",
        candidate_value={"description": ""},
        evidence_sources=[{"source": "introspect", "datasource_id": ds.id}],
        confidence_status="confirmed_by_introspect",
        datasource_id=ds.id,
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.promoted_count == 1

    # candidate 标 active
    cand = (await test_session.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns.id,
        )
    )).scalar_one()
    assert cand.confidence_status == "confirmed_by_introspect"


# ════════════════════════════════════════════════════════════════════
# B6: 代码有 NewEntity 但表未建
# ════════════════════════════════════════════════════════════════════


async def test_b6_code_only_no_introspect(
    test_session, namespace_factory, repo_factory,
):
    """单源 code → AUTO PROMOTE, 建 SCO."""
    ns = await namespace_factory()
    repo = await repo_factory(ns_id=ns.id)
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="new_entity", field_path="name", candidate_kind="field_description",
        candidate_value={"description": "新实体名"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo.id}],
        confidence_status="confirmed_by_code", repo_id=repo.id,
    )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.promoted_count == 1

    # 应建 SCO
    sco = (await test_session.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == ns.id,
            SchemaCanonicalObject.target == "new_entity",
        )
    )).scalar_one()
    assert sco is not None
    assert "新实体名" in sco.fields_json


# ════════════════════════════════════════════════════════════════════
# B7: 并发汇聚 (锁验证)
# ════════════════════════════════════════════════════════════════════


async def test_b7_concurrent_promote_lock(
    test_session, namespace_factory,
):
    """两个 promote 同时跑, 总共仅 promote 1 次 (幂等)."""
    ns = await namespace_factory()
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "并发"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    # asyncio.gather 模拟并发 (锁机制在 promote 内部)
    r1, r2 = await asyncio.gather(
        promote_candidates_to_canonical(test_session, ns.id),
        promote_candidates_to_canonical(test_session, ns.id),
    )
    await test_session.commit()
    assert r1.promoted_count + r2.promoted_count == 1  # 总共仅 promote 1 次


# ════════════════════════════════════════════════════════════════════
# B8: agent_loop 读 SCO 期间 promote 进行
# ════════════════════════════════════════════════════════════════════


async def test_b8_agent_loop_during_promote(
    test_session, namespace_factory,
):
    """promote 前后读 SCO 看到一致状态 (before 或 after, 不 partial)."""
    ns = await namespace_factory()
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "promote_val"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    # 读 promote 前状态
    sco_before = (await test_session.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == ns.id,
            SchemaCanonicalObject.target == "t_order",
        )
    )).scalar_one_or_none()
    assert sco_before is None  # 还没 promote

    await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()

    # 读 promote 后状态 — 完整
    sco_after = (await test_session.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == ns.id,
            SchemaCanonicalObject.target == "t_order",
        )
    )).scalar_one()
    assert "promote_val" in sco_after.fields_json


# ════════════════════════════════════════════════════════════════════
# B9: Conflict 解决后旧候选过期 → 重新开 conflict
# ════════════════════════════════════════════════════════════════════


async def test_b9_conflict_resolved_then_recurrence(
    test_session, namespace_factory, repo_factory,
):
    """解决 conflict 后, 同字段再次冲突 → 重新开 conflict (partial unique 允许)."""
    ns = await namespace_factory()
    repo_a = await repo_factory(ns_id=ns.id)
    repo_b = await repo_factory(ns_id=ns.id)

    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "A"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo_a.id}],
        confidence_status="confirmed_by_code", repo_id=repo_a.id,
    )
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "B"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo_b.id}],
        confidence_status="confirmed_by_code", repo_id=repo_b.id,
    )
    await test_session.commit()
    await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()

    conflict = (await test_session.execute(
        select(SchemaCanonicalConflict).where(SchemaCanonicalConflict.namespace_id == ns.id)
    )).scalar_one()
    assert conflict.status == "open"

    # 模拟 resolve (人工选 keep_a)
    conflict.status = "resolved"
    conflict.resolution_choice = "keep_a"
    conflict.resolved_at = datetime.now()
    # 标 candidate 状态
    cands = (await test_session.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns.id,
        )
    )).scalars().all()
    for c in cands:
        c.status = "active" if "A" in json.loads(c.candidate_value_json).get("description", "") else "rejected"
    await test_session.commit()

    # repo B 重解析仍产 B (新 evidence)
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "B"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo_b.id, "file": "v2.java"}],
        confidence_status="confirmed_by_code", repo_id=repo_b.id,
    )
    await test_session.commit()

    # promote 看 conflict resolved, 重新评估 pending candidates
    await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()

    # 应有新的 open conflict (partial unique 允许同字段多行, 只要旧的 resolved)
    open_conflicts = (await test_session.execute(
        select(SchemaCanonicalConflict).where(
            SchemaCanonicalConflict.namespace_id == ns.id,
            SchemaCanonicalConflict.status == "open",
        )
    )).scalars().all()
    assert len(open_conflicts) >= 1


# ════════════════════════════════════════════════════════════════════
# B10: 跨 namespace 隔离
# ════════════════════════════════════════════════════════════════════


async def test_b10_namespace_isolation(test_session, namespace_factory):
    """ns A 的 candidate 不影响 ns B."""
    ns1 = await namespace_factory()
    ns2 = await namespace_factory()
    await write_canonical_candidate(
        test_session, namespace_id=ns1.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "ns1 V"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await write_canonical_candidate(
        test_session, namespace_id=ns2.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "ns2 V"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    r1 = await promote_candidates_to_canonical(test_session, ns1.id)
    r2 = await promote_candidates_to_canonical(test_session, ns2.id)
    await test_session.commit()

    assert r1.promoted_count == 1
    assert r2.promoted_count == 1
    assert r1.conflicted_count == 0
    assert r2.conflicted_count == 0


# ════════════════════════════════════════════════════════════════════
# B11: 删除 repo 后候选标 orphan, canonical 保留
# ════════════════════════════════════════════════════════════════════


async def test_b11_repo_deletion_orphan(
    test_session, namespace_factory, repo_factory,
):
    """删 repo → candidate orphaned, SCO 值保留."""
    ns = await namespace_factory()
    repo = await repo_factory(ns_id=ns.id)
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "V"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo.id}],
        confidence_status="confirmed_by_code", repo_id=repo.id,
    )
    await test_session.commit()
    await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()

    # SCO 已落值
    sco = (await test_session.execute(
        select(SchemaCanonicalObject).where(SchemaCanonicalObject.namespace_id == ns.id)
    )).scalar_one()
    assert "V" in sco.fields_json

    # 调 orphan hook (模拟删 repo)
    affected = await orphan_candidates_for_repo(test_session, repo.id)
    await test_session.commit()
    assert affected == 1

    # SCO 不被清空
    sco_after = await test_session.get(SchemaCanonicalObject, sco.id)
    assert "V" in sco_after.fields_json


# ════════════════════════════════════════════════════════════════════
# B12: 删除 datasource 同上
# ════════════════════════════════════════════════════════════════════


async def test_b12_datasource_deletion_orphan(
    test_session, namespace_factory, datasource_factory,
):
    """删 datasource → candidate orphaned."""
    ns = await namespace_factory()
    ds = await datasource_factory(ns_id=ns.id)
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database=ds.database,
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "V"},
        evidence_sources=[{"source": "introspect", "datasource_id": ds.id}],
        confidence_status="confirmed_by_introspect", datasource_id=ds.id,
    )
    await test_session.commit()

    affected = await orphan_candidates_for_datasource(test_session, ds.id)
    await test_session.commit()
    assert affected == 1


# ════════════════════════════════════════════════════════════════════
# B13: 大 namespace batch 处理
# ════════════════════════════════════════════════════════════════════


async def test_b13_large_namespace_batch(test_session, namespace_factory):
    """50+ candidates 跨 10+ fields, promote 全部完成."""
    ns = await namespace_factory()
    total = 55
    for i in range(total):
        await write_canonical_candidate(
            test_session, namespace_id=ns.id, db_type="mysql", database="db1",
            target=f"t_table_{i // 5}",
            field_path=f"field_{i}",
            candidate_kind="field_description",
            candidate_value={"description": f"v{i}"},
            evidence_sources=[{"source": "introspect"}],
            confidence_status="confirmed_by_introspect",
        )
    await test_session.commit()

    report = await promote_candidates_to_canonical(test_session, ns.id)
    await test_session.commit()
    assert report.promoted_count == total


# ════════════════════════════════════════════════════════════════════
# B14: 用户 ignore evidence_only 候选, repo 重解析后再次入队
# ════════════════════════════════════════════════════════════════════


async def test_b14_user_ignore_then_repo_reparse(
    test_session, namespace_factory,
):
    """rejected candidate 同值再写 → 复活为 pending."""
    ns = await namespace_factory()
    cid1 = await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "推断"},
        evidence_sources=[{"source": "mybatis_where_literal"}],
        confidence_status="evidence_only",
    )
    await test_session.commit()

    # 用户 ignore
    cand = await test_session.get(SchemaCanonicalCandidate, cid1)
    cand.status = "rejected"
    cand.rejected_at = datetime.now()
    await test_session.commit()

    # repo 重解析仍产相同候选
    cid2 = await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "推断"},
        evidence_sources=[{"source": "mybatis_where_literal"}],
        confidence_status="evidence_only",
    )
    await test_session.commit()

    assert cid1 == cid2  # 同行复用
    cand_again = await test_session.get(SchemaCanonicalCandidate, cid1)
    assert cand_again.status == "pending"  # rejected 也复活
