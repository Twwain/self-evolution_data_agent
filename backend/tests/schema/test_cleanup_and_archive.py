"""Phase 1 Task 8+9: orphan hooks + archive cron tests."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.knowledge.canonical_candidate import write_canonical_candidate
from app.knowledge.candidate_cleanup import (
    archive_old_candidates,
    cleanup_scos_for_datasource,
    orphan_candidates_for_datasource,
    orphan_candidates_for_repo,
)
from app.knowledge.schema_canonical import upsert_schema_canonical
from app.models import SchemaCanonicalAuditLog, SchemaCanonicalCandidate, SchemaCanonicalObject

pytestmark = pytest.mark.asyncio


# ═══════════════════════════════════════════
#  Task 8: orphan hooks
# ═══════════════════════════════════════════


async def test_orphan_candidates_for_repo(
    test_session, namespace_factory, repo_factory,
):
    """删 repo → 该 repo_id 的所有 candidate 标 orphaned, 其他 repo 不受影响."""
    ns = await namespace_factory()
    repo = await repo_factory(ns_id=ns.id)
    other_repo = await repo_factory(ns_id=ns.id)

    # repo 1 写候选
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "状态"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": repo.id}],
        confidence_status="confirmed_by_code", repo_id=repo.id,
    )
    # other repo 写候选 (不应被波及)
    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_user", field_path="name", candidate_kind="field_description",
        candidate_value={"description": "用户名"},
        evidence_sources=[{"source": "code_jpa_javadoc", "repo_id": other_repo.id}],
        confidence_status="confirmed_by_code", repo_id=other_repo.id,
    )
    await test_session.commit()

    affected = await orphan_candidates_for_repo(test_session, repo.id)
    await test_session.commit()
    assert affected == 1

    rows = (await test_session.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns.id
        )
    )).scalars().all()
    by_repo = {c.repo_id: c.status for c in rows}
    assert by_repo[repo.id] == "orphaned"
    assert by_repo[other_repo.id] != "orphaned"


async def test_orphan_candidates_for_datasource(
    test_session, namespace_factory, datasource_factory,
):
    """删 ds → 该 ds 的 candidate 标 orphaned."""
    ns = await namespace_factory()
    ds = await datasource_factory(ns_id=ns.id)

    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="testdb",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "状态"},
        evidence_sources=[{"source": "introspect", "datasource_id": ds.id}],
        confidence_status="confirmed_by_introspect", datasource_id=ds.id,
    )
    await test_session.commit()

    affected = await orphan_candidates_for_datasource(test_session, ds.id)
    await test_session.commit()
    assert affected == 1

    row = (await test_session.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.datasource_id == ds.id
        )
    )).scalar_one()
    assert row.status == "orphaned"


async def test_cleanup_scos_for_datasource(
    test_session, namespace_factory, datasource_factory,
):
    """删 ds → 该 (db_type, database) 的 SCO 被删除 (无其他 ds 共用时)."""
    ns = await namespace_factory()
    ds = await datasource_factory(ns_id=ns.id)

    await upsert_schema_canonical(
        test_session, namespace_id=ns.id, db_type=ds.db_type,
        database=ds.database, target="t_order",
    )
    await test_session.commit()

    deleted = await cleanup_scos_for_datasource(test_session, ds)
    await test_session.commit()
    assert deleted == 1

    remaining = (await test_session.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == ns.id,
            SchemaCanonicalObject.database == ds.database,
        )
    )).scalars().all()
    assert remaining == []


async def test_cleanup_scos_keeps_shared_database(
    test_session, namespace_factory, datasource_factory,
):
    """另一个 ds 共用相同 (db_type, database) → SCO 保留, 不误删."""
    ns = await namespace_factory()
    ds1 = await datasource_factory(ns_id=ns.id)
    ds2 = await datasource_factory(ns_id=ns.id)
    # 让 ds2 共用 ds1 的 (db_type, database)
    ds2.db_type = ds1.db_type
    ds2.database = ds1.database
    await test_session.flush()

    await upsert_schema_canonical(
        test_session, namespace_id=ns.id, db_type=ds1.db_type,
        database=ds1.database, target="t_order",
    )
    await test_session.commit()

    deleted = await cleanup_scos_for_datasource(test_session, ds1)
    await test_session.commit()
    assert deleted == 0

    remaining = (await test_session.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == ns.id,
            SchemaCanonicalObject.database == ds1.database,
        )
    )).scalars().all()
    assert len(remaining) == 1


async def test_orphan_skips_already_orphaned(
    test_session, namespace_factory, repo_factory,
):
    """已经 orphaned/rejected 的 candidate 不重复标记."""
    ns = await namespace_factory()
    repo = await repo_factory(ns_id=ns.id)

    cid = await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "已 orphan"},
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="confirmed_by_code", repo_id=repo.id,
    )
    await test_session.commit()

    # 手动设为 orphaned
    row = await test_session.get(SchemaCanonicalCandidate, cid)
    row.status = "orphaned"
    await test_session.commit()

    affected = await orphan_candidates_for_repo(test_session, repo.id)
    await test_session.commit()
    assert affected == 0


async def test_orphan_writes_audit_log(
    test_session, namespace_factory, repo_factory,
):
    """orphan 流转必有 audit_log 留痕."""
    ns = await namespace_factory()
    repo = await repo_factory(ns_id=ns.id)

    await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "状态"},
        evidence_sources=[{"source": "code_jpa_javadoc"}],
        confidence_status="confirmed_by_code", repo_id=repo.id,
    )
    await test_session.commit()

    await orphan_candidates_for_repo(test_session, repo.id)
    await test_session.commit()

    audits = (await test_session.execute(
        select(SchemaCanonicalAuditLog).where(
            SchemaCanonicalAuditLog.namespace_id == ns.id,
            SchemaCanonicalAuditLog.action == "auto_supersede",
        )
    )).scalars().all()
    assert len(audits) >= 1
    assert "orphan" in (audits[0].reason or "").lower()


# ═══════════════════════════════════════════
#  Task 9: archive cron
# ═══════════════════════════════════════════


async def test_archive_old_superseded_candidates(test_session, namespace_factory):
    """superseded > 90 天 → 物理删除."""
    ns = await namespace_factory()
    cid = await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "old superseded"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    row = await test_session.get(SchemaCanonicalCandidate, cid)
    row.status = "superseded"
    row.updated_at = datetime.now() - timedelta(days=91)
    await test_session.commit()

    result = await archive_old_candidates(test_session)
    await test_session.commit()
    assert result["superseded_archived"] == 1

    row = await test_session.get(SchemaCanonicalCandidate, cid)
    assert row is None


async def test_archive_old_rejected_candidates(test_session, namespace_factory):
    """rejected > 30 天 → 物理删除."""
    ns = await namespace_factory()
    cid = await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "old rejected"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    row = await test_session.get(SchemaCanonicalCandidate, cid)
    row.status = "rejected"
    row.rejected_at = datetime.now() - timedelta(days=31)
    await test_session.commit()

    result = await archive_old_candidates(test_session)
    await test_session.commit()
    assert result["rejected_archived"] == 1

    row = await test_session.get(SchemaCanonicalCandidate, cid)
    assert row is None


async def test_archive_skips_recent_candidates(test_session, namespace_factory):
    """未超期的 superseded/rejected 不删除."""
    ns = await namespace_factory()

    # superseded 只有 10 天
    cid1 = await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="f1", candidate_kind="field_description",
        candidate_value={"description": "recent superseded"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    # rejected 只有 5 天
    cid2 = await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="f2", candidate_kind="field_description",
        candidate_value={"description": "recent rejected"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    row1 = await test_session.get(SchemaCanonicalCandidate, cid1)
    row1.status = "superseded"
    row1.updated_at = datetime.now() - timedelta(days=10)

    row2 = await test_session.get(SchemaCanonicalCandidate, cid2)
    row2.status = "rejected"
    row2.rejected_at = datetime.now() - timedelta(days=5)
    await test_session.commit()

    result = await archive_old_candidates(test_session)
    await test_session.commit()
    assert result["superseded_archived"] == 0
    assert result["rejected_archived"] == 0

    # Both still exist
    assert await test_session.get(SchemaCanonicalCandidate, cid1) is not None
    assert await test_session.get(SchemaCanonicalCandidate, cid2) is not None


async def test_archive_skips_active_candidates(test_session, namespace_factory):
    """active 候选不归档, 即使 updated_at 很久."""
    ns = await namespace_factory()
    cid = await write_canonical_candidate(
        test_session, namespace_id=ns.id, db_type="mysql", database="db1",
        target="t_order", field_path="status", candidate_kind="field_description",
        candidate_value={"description": "active old"},
        evidence_sources=[{"source": "introspect"}],
        confidence_status="confirmed_by_introspect",
    )
    await test_session.commit()

    row = await test_session.get(SchemaCanonicalCandidate, cid)
    row.status = "active"
    row.updated_at = datetime.now() - timedelta(days=200)
    await test_session.commit()

    result = await archive_old_candidates(test_session)
    await test_session.commit()
    assert result["superseded_archived"] == 0
    assert result["rejected_archived"] == 0

    row = await test_session.get(SchemaCanonicalCandidate, cid)
    assert row is not None
