"""引用清理 hook + 归档 — repo/datasource 删除时 orphan + 超期 candidate 物理删.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/plans/2026-05-15-phase1/06-cleanup-and-archive.md

Task 8: orphan_candidates_for_repo / orphan_candidates_for_datasource
Task 9: archive_old_candidates
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.knowledge.canonical_audit import write_canonical_audit_log
from app.models import DataSource, SchemaCanonicalCandidate, SchemaCanonicalObject

log = logging.getLogger(__name__)

# orphan 不影响的终态
_SKIP_STATUSES = ("orphaned", "rejected")


async def orphan_candidates_for_repo(db: AsyncSession, repo_id: int) -> int:
    """把该 repo 关联的所有非终态 candidate 标 orphaned.

    在 delete_repo 之前调用 (FK ON DELETE SET NULL 会清 repo_id, 需先标记).
    返回受影响行数.
    """
    rows = (await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.repo_id == repo_id,
            SchemaCanonicalCandidate.status.notin_(_SKIP_STATUSES),
        )
    )).scalars().all()
    if not rows:
        return 0

    now = datetime.now()
    for c in rows:
        c.status = "orphaned"
        c.updated_at = now
        await write_canonical_audit_log(
            db,
            namespace_id=c.namespace_id,
            action="auto_supersede",
            candidate_id=c.id,
            field_path=c.field_path,
            reason=f"orphaned_by_repo_deletion repo_id={repo_id}",
        )
    await db.flush()
    return len(rows)


async def orphan_candidates_for_datasource(db: AsyncSession, datasource_id: int) -> int:
    """把该 datasource 关联的所有非终态 candidate 标 orphaned."""
    rows = (await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.datasource_id == datasource_id,
            SchemaCanonicalCandidate.status.notin_(_SKIP_STATUSES),
        )
    )).scalars().all()
    if not rows:
        return 0

    now = datetime.now()
    for c in rows:
        c.status = "orphaned"
        c.updated_at = now
        await write_canonical_audit_log(
            db,
            namespace_id=c.namespace_id,
            action="auto_supersede",
            candidate_id=c.id,
            field_path=c.field_path,
            reason=f"orphaned_by_datasource_deletion ds_id={datasource_id}",
        )
    await db.flush()
    return len(rows)


async def cleanup_scos_for_datasource(db: AsyncSession, ds: DataSource) -> int:
    """删除被删数据源对应的 SchemaCanonicalObject 真相源.

    SCO 以 (namespace_id, db_type, database, target) 四元组为键, 不带 datasource_id,
    无法靠外键 CASCADE/SET NULL 清理. 删除数据源时若不主动清, promote 后的 SCO 会
    成为孤儿残留 (前端 Schema 页仍可见).

    安全收敛: 仅当同 namespace 下没有其他数据源共用相同 (db_type, database) 时才删,
    避免多数据源共享同一库时误删. 必须在 db.delete(ds) 之前调用 (此时 ds 仍在 session).

    返回删除的 SCO 数量. 调用方负责 commit.
    """
    # 检查是否有其他数据源共用 (db_type, database)
    sibling = (await db.execute(
        select(DataSource.id).where(
            DataSource.namespace_id == ds.namespace_id,
            DataSource.db_type == ds.db_type,
            DataSource.database == ds.database,
            DataSource.id != ds.id,
        ).limit(1)
    )).scalar_one_or_none()
    if sibling is not None:
        log.info(
            "[cleanup] SCO 保留 — db_type=%s database=%s 仍被 ds=%d 共用",
            ds.db_type, ds.database, sibling,
        )
        return 0

    scos = (await db.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == ds.namespace_id,
            SchemaCanonicalObject.db_type == ds.db_type,
            SchemaCanonicalObject.database == ds.database,
        )
    )).scalars().all()
    if not scos:
        return 0

    for sco in scos:
        await db.delete(sco)
    await db.flush()
    log.info(
        "[cleanup] 删除 %d 个 SCO (ns=%d db_type=%s database=%s ds=%d)",
        len(scos), ds.namespace_id, ds.db_type, ds.database, ds.id,
    )
    return len(scos)


async def archive_old_candidates(db: AsyncSession) -> dict[str, int]:
    """物理删除超期 superseded / rejected candidate.

    - superseded: updated_at < now - candidate_retention_days (90d)
    - rejected: rejected_at < now - candidate_rejected_retention_days (30d)

    Phase 1 简化: 直接 DELETE. Phase 5 升级为归档表.
    返回 {"superseded_archived": N, "rejected_archived": M}.
    """
    now = datetime.now()
    superseded_cutoff = now - timedelta(days=settings.candidate_retention_days)
    rejected_cutoff = now - timedelta(days=settings.candidate_rejected_retention_days)

    # superseded 超期
    rows_superseded = (await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.status == "superseded",
            SchemaCanonicalCandidate.updated_at < superseded_cutoff,
        )
    )).scalars().all()

    # rejected 超期
    rows_rejected = (await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.status == "rejected",
            SchemaCanonicalCandidate.rejected_at < rejected_cutoff,
        )
    )).scalars().all()

    for c in rows_superseded:
        await write_canonical_audit_log(
            db,
            namespace_id=c.namespace_id,
            action="auto_supersede",
            field_path=c.field_path,
            reason=f"archived_purge candidate_id={c.id} status=superseded",
            extra={"original_id": c.id, "original_status": "superseded"},
        )
        await db.delete(c)

    for c in rows_rejected:
        await write_canonical_audit_log(
            db,
            namespace_id=c.namespace_id,
            action="auto_supersede",
            field_path=c.field_path,
            reason=f"archived_purge candidate_id={c.id} status=rejected",
            extra={"original_id": c.id, "original_status": "rejected"},
        )
        await db.delete(c)

    await db.flush()
    result = {
        "superseded_archived": len(rows_superseded),
        "rejected_archived": len(rows_rejected),
    }
    log.info(
        "[archive] deleted %d candidates (superseded=%d, rejected=%d)",
        sum(result.values()), result["superseded_archived"], result["rejected_archived"],
    )
    return result
