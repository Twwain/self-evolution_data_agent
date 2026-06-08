"""Schema canonical candidate writer — 抽取产物入候选层的唯一入口.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/03-extraction.md §3.7+§3.8
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.canonical_audit import write_canonical_audit_log
from app.logging_config import get_logger
from app.models import CandidateKind, ConfidenceStatus, SchemaCanonicalCandidate

log = get_logger(__name__)


def _hash_value(value: dict[str, Any]) -> str:
    """对 candidate_value 做稳定哈希用于 dedup.

    JSON sort_keys 确保 {"a":1,"b":2} 与 {"b":2,"a":1} 哈希一致.
    """
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _merge_evidence(
    existing_json: str, new_sources: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], bool]:
    """合并新来源到既有 evidence_sources, 返回 (merged_list, changed).

    去重键: (source, repo_id|None, file|None, mapper|None, method|None).
    """
    existing: list[dict[str, Any]] = json.loads(existing_json) if existing_json else []
    seen = {
        (s.get("source"), s.get("repo_id"), s.get("file"), s.get("mapper"), s.get("method"))
        for s in existing
    }
    changed = False
    merged = list(existing)
    for src in new_sources:
        key = (
            src.get("source"), src.get("repo_id"), src.get("file"),
            src.get("mapper"), src.get("method"),
        )
        if key not in seen:
            seen.add(key)
            merged.append(src)
            changed = True
    return merged, changed


async def write_canonical_candidate(
    db: AsyncSession,
    *,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
    field_path: str,
    candidate_kind: CandidateKind,
    candidate_value: dict[str, Any],
    evidence_sources: list[dict[str, Any]],
    confidence_status: ConfidenceStatus,
    repo_id: int | None = None,
    datasource_id: int | None = None,
) -> int:
    """UPSERT 一条候选并写 auto_extract 审计.

    返回 candidate id. 调用方负责 commit. 同 value 已存在时, 仅合并 evidence_sources
    并复活 superseded 行 (status → pending).
    """
    value_hash = _hash_value(candidate_value)
    existing = (
        await db.execute(
            select(SchemaCanonicalCandidate).where(
                SchemaCanonicalCandidate.namespace_id == namespace_id,
                SchemaCanonicalCandidate.db_type == db_type,
                SchemaCanonicalCandidate.database == database,
                SchemaCanonicalCandidate.target == target,
                SchemaCanonicalCandidate.field_path == field_path,
                SchemaCanonicalCandidate.candidate_kind == candidate_kind,
                SchemaCanonicalCandidate.value_hash == value_hash,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        merged, changed = _merge_evidence(existing.evidence_sources_json, evidence_sources)
        if changed:
            existing.evidence_sources_json = json.dumps(merged, ensure_ascii=False)
        # 复活: superseded/rejected → pending; 其余状态保持
        if existing.status in ("superseded", "rejected"):
            existing.status = "pending"
            existing.rejected_at = None
        await db.flush()
        await write_canonical_audit_log(
            db,
            namespace_id=namespace_id,
            action="auto_extract",
            candidate_id=existing.id,
            field_path=field_path,
            after={"value_hash": value_hash, "merged_sources": len(merged)},
            reason="dedup_merge_evidence" if changed else "dedup_no_change",
        )
        return existing.id

    # INSERT — savepoint 保护, 防御并发竞态下的 UniqueViolation
    new = SchemaCanonicalCandidate(
        namespace_id=namespace_id,
        db_type=db_type,
        database=database,
        target=target,
        field_path=field_path,
        candidate_kind=candidate_kind,
        candidate_value_json=json.dumps(candidate_value, ensure_ascii=False),
        value_hash=value_hash,
        evidence_sources_json=json.dumps(evidence_sources, ensure_ascii=False),
        status="pending",
        confidence_status=confidence_status,
        repo_id=repo_id,
        datasource_id=datasource_id,
        generation=0,
    )
    try:
        async with db.begin_nested():
            db.add(new)
            await db.flush()
    except IntegrityError:
        # 并发 worker 已 INSERT 同一 (namespace, target, kind, hash) — 走 dedup merge
        log.debug(
            "candidate dedup race: ns=%d target=%s kind=%s hash=%.8s",
            namespace_id, target, candidate_kind, value_hash,
        )
        existing = (
            await db.execute(
                select(SchemaCanonicalCandidate).where(
                    SchemaCanonicalCandidate.namespace_id == namespace_id,
                    SchemaCanonicalCandidate.db_type == db_type,
                    SchemaCanonicalCandidate.database == database,
                    SchemaCanonicalCandidate.target == target,
                    SchemaCanonicalCandidate.field_path == field_path,
                    SchemaCanonicalCandidate.candidate_kind == candidate_kind,
                    SchemaCanonicalCandidate.value_hash == value_hash,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            merged, changed = _merge_evidence(
                existing.evidence_sources_json, evidence_sources,
            )
            if changed:
                existing.evidence_sources_json = json.dumps(merged, ensure_ascii=False)
            if existing.status in ("superseded", "rejected"):
                existing.status = "pending"
                existing.rejected_at = None
            await db.flush()
            await write_canonical_audit_log(
                db,
                namespace_id=namespace_id,
                action="auto_extract",
                candidate_id=existing.id,
                field_path=field_path,
                after={"value_hash": value_hash, "merged_sources": len(merged)},
                reason="dedup_race_merge",
            )
            return existing.id
        raise  # 非 dedup 冲突, 重新抛出

    await write_canonical_audit_log(
        db,
        namespace_id=namespace_id,
        action="auto_extract",
        candidate_id=new.id,
        field_path=field_path,
        after={"value_hash": value_hash, "kind": candidate_kind},
        reason="new_candidate",
    )
    return new.id
