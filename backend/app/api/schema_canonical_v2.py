"""Schema canonical Phase 1 新增端点 — promote / conflicts / candidates / evidence /
confirm-field / lock / unlock / audit-log / pending-counts.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/05-ui-interaction.md §5.7

与 schema_canonical.py (现有 list/get/patch/refresh) 并存.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_ns_manage
from app.config import settings
from app.db.metadata import get_db
from app.engine.tools.probe_tools import inspect_field_values
from app.knowledge.canonical_audit import write_canonical_audit_log
from app.knowledge.canonical_promote import (
    PromoteReport,
    promote_candidates_to_canonical,
    promote_single_field,
)
from app.models import (
    Namespace,
    ResolutionChoice,
    SchemaAuditAction,
    SchemaCanonicalAuditLog,
    SchemaCanonicalCandidate,
    SchemaCanonicalConflict,
    SchemaCanonicalObject,
)
from app.models.user import User

router = APIRouter(
    prefix="/api/namespaces/{ns_id}/schema-canonical",
    tags=["schema-canonical-v2"],
)


# ════════════════════════════════════════════════════════════════════
# Request / Response Schemas
# ════════════════════════════════════════════════════════════════════


class PromoteBody(BaseModel):
    force: bool = False


class PromoteResponse(BaseModel):
    promoted_count: int
    conflicted_count: int
    skipped_user_locked: int
    skipped_in_conflict: int
    candidates_processed: int
    duration_seconds: float

    @classmethod
    def from_report(cls, r: PromoteReport) -> "PromoteResponse":
        return cls(
            promoted_count=r.promoted_count,
            conflicted_count=r.conflicted_count,
            skipped_user_locked=r.skipped_user_locked,
            skipped_in_conflict=r.skipped_in_conflict,
            candidates_processed=r.candidates_processed,
            duration_seconds=r.duration_seconds,
        )


class ConflictOut(BaseModel):
    id: int
    db_type: str
    database: str
    target: str
    field_path: str
    candidate_kind: str
    conflict_type: str
    candidates_snapshot: list[dict]
    status: str
    resolution_choice: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime


class ConflictResolveBody(BaseModel):
    resolution_choice: ResolutionChoice
    resolution_value: dict | None = None
    candidate_id: int | None = None
    reason: str | None = None


class CandidateOut(BaseModel):
    id: int
    field_path: str
    candidate_kind: str
    candidate_value: dict
    evidence_sources: list[dict]
    status: str
    confidence_status: str
    repo_id: int | None = None
    datasource_id: int | None = None
    created_at: datetime
    updated_at: datetime


class ConfirmFieldBody(BaseModel):
    field_path: str
    action: Literal["confirm", "correct", "ignore"]
    corrected_value: dict | None = None
    reason: str | None = None


class LockBody(BaseModel):
    field_path: str | None = None
    reason: str | None = None


class UnlockBody(BaseModel):
    field_path: str | None = None


class PendingCountsOut(BaseModel):
    pending_promote: int
    evidence_only: int
    conflicts: int
    audit_today: int


class AuditLogOut(BaseModel):
    id: int
    action: str
    field_path: str | None = None
    candidate_id: int | None = None
    conflict_id: int | None = None
    canonical_id: int | None = None
    before: dict | None = None
    after: dict | None = None
    reason: str | None = None
    actor_id: int | None = None
    extra: dict | None = None
    created_at: datetime


# ════════════════════════════════════════════════════════════════════
# Endpoints
# ════════════════════════════════════════════════════════════════════


@router.post("/promote", response_model=PromoteResponse)
async def promote_endpoint(
    ns_id: int,
    body: PromoteBody | None = None,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> PromoteResponse:
    """手动触发 ns-wide promote."""
    await _verify_namespace(db, ns_id)
    report = await promote_candidates_to_canonical(db, ns_id)
    await db.commit()
    return PromoteResponse.from_report(report)


@router.get("/conflicts", response_model=list[ConflictOut])
async def list_conflicts(
    ns_id: int,
    status: Literal["open", "resolved", "all"] = Query("open"),
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> list[ConflictOut]:
    await _verify_namespace(db, ns_id)
    q = select(SchemaCanonicalConflict).where(
        SchemaCanonicalConflict.namespace_id == ns_id,
    )
    if status != "all":
        q = q.where(SchemaCanonicalConflict.status == status)
    rows = (await db.execute(
        q.order_by(SchemaCanonicalConflict.created_at.desc())
    )).scalars().all()
    return [_conflict_to_out(r) for r in rows]


@router.get("/conflicts/{cid}", response_model=ConflictOut)
async def get_conflict(
    ns_id: int,
    cid: int,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> ConflictOut:
    row = await db.get(SchemaCanonicalConflict, cid)
    if not row or row.namespace_id != ns_id:
        raise HTTPException(404, "conflict not found")
    return _conflict_to_out(row)


@router.post("/conflicts/{cid}/resolve", response_model=ConflictOut)
async def resolve_conflict(
    ns_id: int,
    cid: int,
    body: ConflictResolveBody,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> ConflictOut:
    row = await db.get(SchemaCanonicalConflict, cid)
    if not row or row.namespace_id != ns_id:
        raise HTTPException(404, "conflict not found")
    if row.status == "resolved":
        raise HTTPException(409, "conflict already resolved")

    candidate_ids = json.loads(row.candidate_ids_json)
    cands = list((await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.id.in_(candidate_ids)
        )
    )).scalars().all())

    if body.resolution_choice in ("keep_a", "keep_b"):
        if body.candidate_id is not None:
            winner = next((c for c in cands if c.id == body.candidate_id), None)
            if winner is None:
                raise HTTPException(422, f"candidate_id={body.candidate_id} not in conflict")
        elif len(cands) > 2:
            raise HTTPException(
                422,
                "candidate_id is required when conflict has more than 2 candidates",
            )
        elif body.resolution_choice == "keep_a":
            winner = cands[0]
        else:  # keep_b, len(cands) == 2
            winner = cands[1]
        losers = [c for c in cands if c.id != winner.id]
    elif body.resolution_choice == "merge":
        if not body.resolution_value:
            raise HTTPException(422, "merge requires resolution_value")
        cands[0].candidate_value_json = json.dumps(
            body.resolution_value, ensure_ascii=False
        )
        winner = cands[0]
        losers = cands[1:]
    else:  # reject_all
        for c in cands:
            c.status = "rejected"
            c.rejected_at = datetime.now()
        row.status = "resolved"
        row.resolution_choice = body.resolution_choice
        row.resolved_by = user.id
        row.resolved_at = datetime.now()
        row.resolution_reason = body.reason
        await write_canonical_audit_log(
            db, namespace_id=ns_id, action="conflict_resolve_reject",
            conflict_id=cid, field_path=row.field_path,
            actor_id=user.id, reason=body.reason,
        )
        await db.commit()
        return _conflict_to_out(row)

    # winner promote, losers reject
    winner.status = "pending"
    for loser in losers:
        loser.status = "rejected"
        loser.rejected_at = datetime.now()

    row.status = "resolved"
    row.resolution_choice = body.resolution_choice
    row.resolution_value_json = (
        json.dumps(body.resolution_value, ensure_ascii=False)
        if body.resolution_value else None
    )
    row.resolved_by = user.id
    row.resolved_at = datetime.now()
    row.resolution_reason = body.reason

    action_map: dict[str, SchemaAuditAction] = {
        "keep_a": "conflict_resolve_keep_a",
        "keep_b": "conflict_resolve_keep_b",
        "merge": "conflict_resolve_merge",
    }
    await write_canonical_audit_log(
        db, namespace_id=ns_id, action=action_map[body.resolution_choice],
        conflict_id=cid, field_path=row.field_path,
        actor_id=user.id, reason=body.reason,
    )
    await db.flush()

    await promote_single_field(
        db, ns_id=ns_id, db_type=row.db_type, database=row.database,
        target=row.target, field_path=row.field_path,
        candidate_kind=row.candidate_kind,
    )
    await db.commit()
    return _conflict_to_out(row)


# ════════════════════════════════════════════════════════════════════
# Phase 3 收尾 — pending-candidates / evidence-only 聚合视图
# (must be before {sco_id} routes to avoid path param capture)
# ════════════════════════════════════════════════════════════════════


@router.get("/pending-candidates")
async def list_pending_candidates(
    ns_id: int,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """待汇聚候选聚合视图 — 按 (target, field_path, candidate_kind) 分组."""
    await _verify_namespace(db, ns_id)
    rows = (await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
            SchemaCanonicalCandidate.status == "pending",
        ).order_by(
            SchemaCanonicalCandidate.target,
            SchemaCanonicalCandidate.field_path,
            SchemaCanonicalCandidate.candidate_kind,
        )
    )).scalars().all()

    from itertools import groupby
    from operator import attrgetter

    groups: list[dict] = []
    for (target, field_path, kind), group_iter in groupby(
        rows, key=attrgetter("target", "field_path", "candidate_kind")
    ):
        cands = list(group_iter)
        groups.append({
            "id": cands[0].id,
            "target": target,
            "field_path": field_path,
            "candidate_kind": kind,
            "candidates": [
                {
                    "id": c.id,
                    "source": json.loads(c.evidence_sources_json)[0].get("source", "unknown")
                    if c.evidence_sources_json else "unknown",
                    "value": json.loads(c.candidate_value_json),
                }
                for c in cands
            ],
        })
    return groups


@router.get("/evidence-only")
async def list_evidence_only_fields(
    ns_id: int,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """待确认字段列表 — confidence_status=evidence_only 且 status=pending."""
    await _verify_namespace(db, ns_id)
    rows = (await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
            SchemaCanonicalCandidate.status == "pending",
            SchemaCanonicalCandidate.confidence_status == "evidence_only",
        ).order_by(SchemaCanonicalCandidate.target, SchemaCanonicalCandidate.field_path)
    )).scalars().all()

    seen: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r.target, r.field_path)
        if key in seen:
            continue
        sco_id = (await db.execute(
            select(SchemaCanonicalObject.id).where(
                SchemaCanonicalObject.namespace_id == ns_id,
                SchemaCanonicalObject.db_type == r.db_type,
                SchemaCanonicalObject.database == r.database,
                SchemaCanonicalObject.target == r.target,
            )
        )).scalar_one_or_none()

        value = json.loads(r.candidate_value_json)
        evidence = json.loads(r.evidence_sources_json) if r.evidence_sources_json else []
        seen[key] = {
            "sco_id": sco_id,
            "target": r.target,
            "field_path": r.field_path,
            "current_value": value,
            "evidence_summary": f"{len(evidence)} 个证据源",
        }
    return list(seen.values())


@router.get("/{sco_id}/candidates", response_model=list[CandidateOut])
async def list_candidates(
    ns_id: int,
    sco_id: int,
    field_path: str | None = Query(None),
    status: str | None = Query(None),
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> list[CandidateOut]:
    sco = await db.get(SchemaCanonicalObject, sco_id)
    if not sco or sco.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")
    q = select(SchemaCanonicalCandidate).where(
        SchemaCanonicalCandidate.namespace_id == ns_id,
        SchemaCanonicalCandidate.db_type == sco.db_type,
        SchemaCanonicalCandidate.database == sco.database,
        SchemaCanonicalCandidate.target == sco.target,
    )
    if field_path is not None:
        q = q.where(SchemaCanonicalCandidate.field_path == field_path)
    if status is not None:
        q = q.where(SchemaCanonicalCandidate.status == status)
    rows = (await db.execute(
        q.order_by(SchemaCanonicalCandidate.created_at.desc())
    )).scalars().all()
    return [_cand_to_out(r) for r in rows]


@router.get("/{sco_id}/evidence")
async def evidence_endpoint(
    ns_id: int,
    sco_id: int,
    field: str = Query(..., description="字段路径"),
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """返回该字段的所有 candidate evidence."""
    sco = await db.get(SchemaCanonicalObject, sco_id)
    if not sco or sco.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")
    cands = (await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
            SchemaCanonicalCandidate.db_type == sco.db_type,
            SchemaCanonicalCandidate.database == sco.database,
            SchemaCanonicalCandidate.target == sco.target,
            SchemaCanonicalCandidate.field_path == field,
        )
    )).scalars().all()
    return {
        "field_path": field,
        "candidates": [_cand_to_out(c).model_dump(mode="json") for c in cands],
        "canonical_value": _extract_canonical_field(sco, field),
    }


@router.post("/{sco_id}/confirm-field")
async def confirm_field(
    ns_id: int,
    sco_id: int,
    body: ConfirmFieldBody,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """evidence_only 候选的人工确认."""
    sco = await db.get(SchemaCanonicalObject, sco_id)
    if not sco or sco.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")

    if body.action == "correct" and body.corrected_value is None:
        raise HTTPException(422, "correct action requires corrected_value")

    cands = list((await db.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
            SchemaCanonicalCandidate.db_type == sco.db_type,
            SchemaCanonicalCandidate.database == sco.database,
            SchemaCanonicalCandidate.target == sco.target,
            SchemaCanonicalCandidate.field_path == body.field_path,
            SchemaCanonicalCandidate.confidence_status == "evidence_only",
            SchemaCanonicalCandidate.status == "pending",
        )
    )).scalars().all())
    if not cands:
        raise HTTPException(404, "no evidence_only candidate for this field")

    for c in cands:
        if body.action == "confirm":
            c.confidence_status = "confirmed_by_user"
            c.status = "pending"
            audit_action = "user_confirm"
        elif body.action == "correct":
            c.candidate_value_json = json.dumps(
                body.corrected_value, ensure_ascii=False
            )
            c.confidence_status = "confirmed_by_user"
            c.status = "pending"
            audit_action = "user_correct"
        else:  # ignore
            c.status = "rejected"
            c.rejected_at = datetime.now()
            audit_action = "user_ignore"

        await write_canonical_audit_log(
            db, namespace_id=ns_id, action=audit_action,
            candidate_id=c.id, canonical_id=sco_id,
            field_path=body.field_path,
            actor_id=user.id, reason=body.reason,
        )

    await db.flush()

    if body.action in ("confirm", "correct"):
        await promote_single_field(
            db, ns_id=ns_id, db_type=sco.db_type, database=sco.database,
            target=sco.target, field_path=body.field_path,
            candidate_kind="field_description",
        )
    await db.commit()
    return {"ok": True, "affected_candidates": len(cands)}


@router.post("/{sco_id}/lock")
async def lock_field(
    ns_id: int,
    sco_id: int,
    body: LockBody,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sco = await db.get(SchemaCanonicalObject, sco_id)
    if not sco or sco.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")
    sco.user_locked = True
    sco.updated_at = datetime.now()
    await write_canonical_audit_log(
        db, namespace_id=ns_id, action="user_lock",
        canonical_id=sco_id, field_path=body.field_path,
        actor_id=user.id, reason=body.reason,
    )
    await db.commit()
    return {"ok": True}


@router.post("/{sco_id}/unlock")
async def unlock_field(
    ns_id: int,
    sco_id: int,
    body: UnlockBody,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sco = await db.get(SchemaCanonicalObject, sco_id)
    if not sco or sco.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")
    sco.user_locked = False
    sco.updated_at = datetime.now()
    await write_canonical_audit_log(
        db, namespace_id=ns_id, action="user_unlock",
        canonical_id=sco_id, field_path=body.field_path,
        actor_id=user.id,
    )
    await db.commit()
    return {"ok": True}


@router.get("/audit-log", response_model=list[AuditLogOut])
async def audit_log_endpoint(
    ns_id: int,
    action: list[str] | None = Query(None),
    actor: int | None = Query(None),
    field_path: str | None = Query(None),
    sco_id: int | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(
        default=settings.schema_audit_log_page_default,
        le=settings.schema_audit_log_page_max,
    ),
    cursor: int | None = Query(None, description="last seen id for pagination"),
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> list[AuditLogOut]:
    await _verify_namespace(db, ns_id)
    q = select(SchemaCanonicalAuditLog).where(
        SchemaCanonicalAuditLog.namespace_id == ns_id,
    )
    if action:
        q = q.where(SchemaCanonicalAuditLog.action.in_(action))
    if actor is not None:
        q = q.where(SchemaCanonicalAuditLog.actor_id == actor)
    if field_path:
        q = q.where(SchemaCanonicalAuditLog.field_path == field_path)
    if sco_id:
        q = q.where(SchemaCanonicalAuditLog.canonical_id == sco_id)
    if since:
        q = q.where(SchemaCanonicalAuditLog.created_at >= since)
    if until:
        q = q.where(SchemaCanonicalAuditLog.created_at <= until)
    if cursor:
        q = q.where(SchemaCanonicalAuditLog.id < cursor)

    rows = (await db.execute(
        q.order_by(SchemaCanonicalAuditLog.id.desc()).limit(limit)
    )).scalars().all()
    return [_audit_to_out(r) for r in rows]


@router.get("/pending-counts", response_model=PendingCountsOut)
async def pending_counts(
    ns_id: int,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> PendingCountsOut:
    """UI 顶部计数徽章数据源."""
    await _verify_namespace(db, ns_id)
    pending_promote = (await db.execute(
        select(func.count()).select_from(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
            SchemaCanonicalCandidate.status == "pending",
        )
    )).scalar_one()
    evidence_only = (await db.execute(
        select(func.count()).select_from(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
            SchemaCanonicalCandidate.status == "pending",
            SchemaCanonicalCandidate.confidence_status == "evidence_only",
        )
    )).scalar_one()
    conflicts = (await db.execute(
        select(func.count()).select_from(SchemaCanonicalConflict).where(
            SchemaCanonicalConflict.namespace_id == ns_id,
            SchemaCanonicalConflict.status == "open",
        )
    )).scalar_one()
    today_start = datetime.combine(date.today(), datetime.min.time())
    audit_today = (await db.execute(
        select(func.count()).select_from(SchemaCanonicalAuditLog).where(
            SchemaCanonicalAuditLog.namespace_id == ns_id,
            SchemaCanonicalAuditLog.created_at >= today_start,
        )
    )).scalar_one()
    return PendingCountsOut(
        pending_promote=pending_promote,
        evidence_only=evidence_only,
        conflicts=conflicts,
        audit_today=audit_today,
    )


# ════════════════════════════════════════════════════════════════════
# Phase 2 Plan 04 — pending_enum_binding (must be before {sco_id} routes)
# ════════════════════════════════════════════════════════════════════


@router.get("/fields/pending_enum_binding")
async def pending_enum_binding(
    ns_id: int,
    namespace_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Scan all SCO fields_json for enum_match_status='pending'."""
    target_ns = namespace_id if namespace_id is not None else ns_id
    scos = (await db.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == target_ns,
        )
    )).scalars().all()

    items: list[dict] = []
    for sco in scos:
        if not sco.fields_json:
            continue
        fields = json.loads(sco.fields_json)
        for f in fields:
            if f.get("enum_match_status") == "pending":
                items.append({
                    "collection_id": sco.id,
                    "collection_name": sco.target,
                    "field": f.get("name", ""),
                    "field_type": f.get("type"),
                    "enum_class_hint": f.get("enum_class_hint"),
                    "sample_values": f.get("sample_values"),
                })

    total = len(items)
    start = (page - 1) * size
    end = start + size
    return {"items": items[start:end], "total": total}


# ════════════════════════════════════════════════════════════════════
# Phase 2 Plan 04 — bind / unbind / inspect_samples
# ════════════════════════════════════════════════════════════════════


class _BindEnumBody(BaseModel):
    enum_dict_id: int
    force: bool = False


@router.post("/{sco_id}/fields/{field_name}/bind_enum")
async def bind_field_to_enum(
    ns_id: int,
    sco_id: int,
    field_name: str,
    body: _BindEnumBody,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Bind field to EnumDictionary with sample_values coverage check."""
    from app.models.enum_dictionary import EnumDictionary

    sco = await db.get(SchemaCanonicalObject, sco_id)
    if not sco or sco.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")

    enum_row = await db.get(EnumDictionary, body.enum_dict_id)
    if not enum_row or enum_row.namespace_id != ns_id:
        raise HTTPException(404, "enum dictionary not found or different namespace")

    fields = json.loads(sco.fields_json) if sco.fields_json else []
    field_entry = next((f for f in fields if f.get("name") == field_name), None)
    if field_entry is None:
        raise HTTPException(404, f"field '{field_name}' not found")

    # Parse enum values
    enum_values = json.loads(enum_row.values_json) if enum_row.values_json else []
    enum_db_values = {v["db_value"] for v in enum_values}

    # Coverage check
    sample_values = field_entry.get("sample_values")
    match_status = "matched"
    if sample_values:
        uncovered = {v for v in sample_values if v not in enum_db_values}
        if uncovered:
            if not body.force:
                raise HTTPException(
                    422,
                    f"字段样本 {uncovered} 不在 enum 值集合 {enum_db_values} 中, "
                    f"传 force=true 强制绑定 (将进 conflict 队列)",
                )
            match_status = "conflict"

    # Write fields_json
    field_entry["enum_ref_id"] = body.enum_dict_id
    field_entry["enum_values"] = enum_values
    field_entry["enum_source"] = "manual_binding"
    field_entry["enum_match_status"] = match_status

    sco.fields_json = json.dumps(fields, ensure_ascii=False)
    sco.updated_at = datetime.now()
    await db.commit()

    await write_canonical_audit_log(
        db, namespace_id=ns_id, action="field_enum_manual_bind",
        canonical_id=sco_id, field_path=field_name,
        actor_id=user.id,
        after={"enum_dict_id": body.enum_dict_id, "match_status": match_status},
    )
    await db.commit()

    return {
        "field": field_name,
        "enum_ref_id": body.enum_dict_id,
        "enum_source": "manual_binding",
        "enum_match_status": match_status,
    }


@router.delete("/{sco_id}/fields/{field_name}/bind_enum")
async def unbind_field_enum(
    ns_id: int,
    sco_id: int,
    field_name: str,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Unbind field from enum, revert to pending if field has enum suffix."""
    from app.knowledge.enum_extractor import ENUM_NAME_SUFFIXES, _split_camel
    from app.knowledge.enum_sync import _resolve_open_conflicts

    sco = await db.get(SchemaCanonicalObject, sco_id)
    if not sco or sco.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")

    fields = json.loads(sco.fields_json) if sco.fields_json else []
    field_entry = next((f for f in fields if f.get("name") == field_name), None)
    if field_entry is None:
        raise HTTPException(404, f"field '{field_name}' not found")

    prior_enum_ref_id = field_entry.get("enum_ref_id")

    # Clear enum binding attrs
    for key in ("enum_ref_id", "enum_values", "enum_source"):
        field_entry.pop(key, None)

    # Check if field name has enum suffix → revert to pending
    tokens = _split_camel(field_name)
    has_suffix = (
        len(tokens) >= 2
        and (tokens[-1][:1].upper() + tokens[-1][1:]) in ENUM_NAME_SUFFIXES
    )
    if has_suffix:
        field_entry["enum_match_status"] = "pending"
    else:
        field_entry.pop("enum_match_status", None)

    # 同事务关闭该 (field, enum) 仍 open 的 conflict, 防 UI 残留孤儿
    if isinstance(prior_enum_ref_id, int):
        await _resolve_open_conflicts(
            db,
            field_canonical_id=sco_id,
            field_name=field_name,
            enum_dict_id=prior_enum_ref_id,
            resolver_id=user.id,
        )

    sco.fields_json = json.dumps(fields, ensure_ascii=False)
    sco.updated_at = datetime.now()
    await db.commit()

    await write_canonical_audit_log(
        db, namespace_id=ns_id, action="field_enum_manual_unbind",
        canonical_id=sco_id, field_path=field_name,
        actor_id=user.id,
    )
    await db.commit()

    return {
        "field": field_name,
        "enum_match_status": field_entry.get("enum_match_status"),
    }


class _InspectSamplesBody(BaseModel):
    limit: int = Field(default=50, ge=1)


@router.post("/{sco_id}/fields/{field_name}/inspect_samples")
async def inspect_samples_endpoint(
    ns_id: int,
    sco_id: int,
    field_name: str,
    body: _InspectSamplesBody | None = None,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Call inspect_field_values and write sample_values to fields_json."""
    limit = body.limit if body else settings.field_sample_default_limit
    if limit > settings.field_sample_max_limit:
        raise HTTPException(422, f"limit 不能超过 {settings.field_sample_max_limit}")

    sco = await db.get(SchemaCanonicalObject, sco_id)
    if not sco or sco.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")

    if sco.db_type != "mongodb":
        raise HTTPException(
            422,
            f"inspect_samples 仅支持 mongodb (当前 db_type={sco.db_type}); "
            "MySQL 字段样本采集走 `SELECT DISTINCT ... LIMIT N` 路径, 暂未实现",
        )

    fields = json.loads(sco.fields_json) if sco.fields_json else []
    field_entry = next((f for f in fields if f.get("name") == field_name), None)
    if field_entry is None:
        raise HTTPException(404, f"field '{field_name}' not found")

    # Call inspect_field_values
    result = await inspect_field_values(
        namespace_id=ns_id,
        collection=sco.target,
        field=field_name,
        database=sco.database,
        sample=limit,
    )

    values = result.get("values", [])
    distinct_count = len(set(str(v) for v in values))
    is_complete = distinct_count <= limit

    # Write to fields_json
    field_entry["sample_values"] = values
    field_entry["sample_metadata"] = {
        "distinct_count": distinct_count,
        "scanned_doc_count": limit,
        "is_complete": is_complete,
    }

    sco.fields_json = json.dumps(fields, ensure_ascii=False)
    sco.updated_at = datetime.now()
    await db.commit()

    await write_canonical_audit_log(
        db, namespace_id=ns_id, action="field_sample_collected",
        canonical_id=sco_id, field_path=field_name,
        actor_id=user.id,
        after={"distinct_count": distinct_count, "is_complete": is_complete},
    )
    await db.commit()

    return {
        "field": field_name,
        "sample_values": values,
        "distinct_count": distinct_count,
        "scanned_doc_count": limit,
        "is_complete": is_complete,
    }


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════


async def _verify_namespace(db: AsyncSession, ns_id: int) -> Namespace:
    ns = await db.get(Namespace, ns_id)
    if not ns:
        raise HTTPException(404, "namespace not found")
    return ns


def _conflict_to_out(c: SchemaCanonicalConflict) -> ConflictOut:
    return ConflictOut(
        id=c.id,
        db_type=c.db_type,
        database=c.database,
        target=c.target,
        field_path=c.field_path,
        candidate_kind=c.candidate_kind,
        conflict_type=c.conflict_type,
        candidates_snapshot=json.loads(c.candidates_snapshot_json),
        status=c.status,
        resolution_choice=c.resolution_choice,
        resolved_at=c.resolved_at,
        created_at=c.created_at,
    )


def _cand_to_out(c: SchemaCanonicalCandidate) -> CandidateOut:
    return CandidateOut(
        id=c.id,
        field_path=c.field_path,
        candidate_kind=c.candidate_kind,
        candidate_value=json.loads(c.candidate_value_json),
        evidence_sources=json.loads(c.evidence_sources_json),
        status=c.status,
        confidence_status=c.confidence_status,
        repo_id=c.repo_id,
        datasource_id=c.datasource_id,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _audit_to_out(a: SchemaCanonicalAuditLog) -> AuditLogOut:
    return AuditLogOut(
        id=a.id,
        action=a.action,
        field_path=a.field_path,
        candidate_id=a.candidate_id,
        conflict_id=a.conflict_id,
        canonical_id=a.canonical_id,
        before=json.loads(a.before_json) if a.before_json else None,
        after=json.loads(a.after_json) if a.after_json else None,
        reason=a.reason,
        actor_id=a.actor_id,
        extra=json.loads(a.extra_json) if a.extra_json else None,
        created_at=a.created_at,
    )


def _extract_canonical_field(sco: SchemaCanonicalObject, field_path: str) -> dict | None:
    fields = json.loads(sco.fields_json) if sco.fields_json else []
    for f in fields:
        if f.get("name") == field_path:
            return f
    return None
