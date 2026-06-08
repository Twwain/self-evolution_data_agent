"""EnumDictionary CRUD — 5 端点.

设计: docs/superpowers/specs/2026-05-18-enum-knowledge-binding/03-enum-dictionary.md §4
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_admin
from app.config import settings
from app.db.metadata import get_db
from app.knowledge.canonical_audit import write_canonical_audit_log
from app.models.enum_dictionary import EnumDictionary
from app.models.schema_canonical_object import SchemaCanonicalObject
from app.models.user import User
from app.schemas.enum_dictionary import (
    EnumDictionaryCreate,
    EnumDictionaryResponse,
    EnumDictionaryUpdate,
    EnumValueItem,
)

router = APIRouter(prefix="/api/enum-dictionary", tags=["enum-dictionary"])


# ════════════════════════════════════════════════════════════════════
# Request / Response schemas (endpoint-local)
# ════════════════════════════════════════════════════════════════════


class _CreateResponse(BaseModel):
    id: int
    source: str


class _DeleteDryRunResponse(BaseModel):
    affected_fields: list[dict]
    confirm_token: str | None = None


class _ReferenceItem(BaseModel):
    collection_id: int
    collection_name: str
    field: str
    field_type: str | None = None
    enum_match_status: str | None = None


class _ReferencesResponse(BaseModel):
    items: list[_ReferenceItem]
    total: int


class _ListResponse(BaseModel):
    items: list[EnumDictionaryResponse]
    total: int


# ════════════════════════════════════════════════════════════════════
# Endpoints
# ════════════════════════════════════════════════════════════════════


@router.get("", response_model=_ListResponse)
async def list_enum_dictionaries(
    namespace_id: int = Query(..., gt=0),
    source: str = Query("all"),
    name_like: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> _ListResponse:
    """列表 + reference_count."""
    q = select(EnumDictionary).where(EnumDictionary.namespace_id == namespace_id)
    if source != "all":
        q = q.where(EnumDictionary.source == source)
    if name_like:
        q = q.where(EnumDictionary.enum_class_name.contains(name_like))
    rows = list((await db.execute(q.order_by(EnumDictionary.id.desc()))).scalars().all())

    # reference_count: scan all SCO fields_json for enum_ref_id
    ref_counts = await _compute_reference_counts(db, namespace_id, [r.id for r in rows])

    items = [
        _row_to_response(r, ref_counts.get(r.id, 0)) for r in rows
    ]
    return _ListResponse(items=items, total=len(items))


@router.post("", response_model=_CreateResponse, status_code=201)
async def create_enum_dictionary(
    body: EnumDictionaryCreate,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> _CreateResponse:
    """创建 manual enum, 409 on duplicate."""
    existing = (await db.execute(
        select(EnumDictionary).where(
            EnumDictionary.namespace_id == body.namespace_id,
            EnumDictionary.enum_class_name == body.enum_class_name,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(
            409,
            f"enum_class_name 已存在 (id={existing.id}, source={existing.source})",
        )

    row = EnumDictionary(
        namespace_id=body.namespace_id,
        enum_class_name=body.enum_class_name,
        fully_qualified_name=body.fully_qualified_name,
        values_json=json.dumps(
            [v.model_dump() for v in body.values], ensure_ascii=False
        ),
        scope=body.scope,
        source="manual",
        comment=body.comment or "",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    await _try_enqueue_sync(db, row.id, row.namespace_id, "create")

    return _CreateResponse(id=row.id, source="manual")


@router.put("/{enum_id}", response_model=EnumDictionaryResponse)
async def update_enum_dictionary(
    enum_id: int,
    body: EnumDictionaryUpdate,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> EnumDictionaryResponse:
    """编辑 values/comment, source='code' 自动转 'manual'."""
    row = await db.get(EnumDictionary, enum_id)
    if not row:
        raise HTTPException(404, "enum dictionary not found")

    if body.values is not None:
        row.values_json = json.dumps(
            [v.model_dump() for v in body.values], ensure_ascii=False
        )
    if body.comment is not None:
        row.comment = body.comment

    # code → manual 自动升级
    if row.source == "code":
        row.source = "manual"
    row.updated_by = user.id
    row.updated_at = datetime.now()
    await db.commit()
    await db.refresh(row)

    await _try_enqueue_sync(db, row.id, row.namespace_id, "update")

    ref_counts = await _compute_reference_counts(db, row.namespace_id, [row.id])
    return _row_to_response(row, ref_counts.get(row.id, 0))


@router.delete("/{enum_id}")
async def delete_enum_dictionary(
    enum_id: int,
    dry_run: bool = Query(True),
    confirm_token: str | None = Query(None),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除 enum dictionary, dry_run + confirm_token pattern."""
    row = await db.get(EnumDictionary, enum_id)
    if not row:
        raise HTTPException(404, "enum dictionary not found")

    affected = await _find_affected_fields(db, row.namespace_id, enum_id)

    if dry_run:
        token = _make_confirm_token(enum_id, len(affected)) if (
            len(affected) > settings.bulk_op_require_confirm_above
        ) else None
        return {"affected_fields": affected, "confirm_token": token}

    # Non-dry-run: check confirm_token if needed
    if len(affected) > settings.bulk_op_require_confirm_above:
        expected = _make_confirm_token(enum_id, len(affected))
        if confirm_token != expected:
            raise HTTPException(422, "confirm_token required for bulk delete")

    ns_id = row.namespace_id
    enum_class_name = row.enum_class_name
    affected_count = len(affected)

    await db.delete(row)
    # 同事务先写 audit_log, 防 enqueue / 进程崩溃丢事件;
    # cascade unbind 仍由 enum_sync_loop 异步处理 (best-effort, 失败有 audit 锚点可重放).
    await write_canonical_audit_log(
        db,
        namespace_id=ns_id,
        action="enum_dict_deleted",
        actor_id=user.id,
        before={
            "enum_dict_id": enum_id,
            "enum_class_name": enum_class_name,
            "affected_field_count": affected_count,
        },
        reason=(
            f"manual delete enum_dict_id={enum_id} ({enum_class_name}) "
            f"affecting {affected_count} field reference(s); "
            "cascade unbind dispatched to enum_sync_loop"
        ),
    )
    await db.commit()

    await _try_enqueue_sync(db, enum_id, ns_id, "delete")

    return {"ok": True, "deleted_id": enum_id, "affected_fields": affected_count}


@router.get("/{enum_id}/references", response_model=_ReferencesResponse)
async def get_references(
    enum_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> _ReferencesResponse:
    """反向扫 fields_json 找 enum_ref_id == id."""
    row = await db.get(EnumDictionary, enum_id)
    if not row:
        raise HTTPException(404, "enum dictionary not found")

    items = await _find_reference_items(db, row.namespace_id, enum_id)
    return _ReferencesResponse(items=items, total=len(items))


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════


def _row_to_response(row: EnumDictionary, reference_count: int) -> EnumDictionaryResponse:
    values = json.loads(row.values_json) if row.values_json else []
    return EnumDictionaryResponse(
        id=row.id,
        namespace_id=row.namespace_id,
        enum_class_name=row.enum_class_name,
        fully_qualified_name=row.fully_qualified_name,
        values=[EnumValueItem(**v) for v in values],
        scope=row.scope,
        source=row.source,
        comment=row.comment or "",
        reference_count=reference_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _compute_reference_counts(
    db: AsyncSession, namespace_id: int, enum_ids: list[int],
) -> dict[int, int]:
    """Scan SCO fields_json for enum_ref_id occurrences."""
    if not enum_ids:
        return {}
    counts: dict[int, int] = {eid: 0 for eid in enum_ids}
    id_set = set(enum_ids)
    scos = (await db.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == namespace_id,
        )
    )).scalars().all()
    for sco in scos:
        if not sco.fields_json:
            continue
        fields = json.loads(sco.fields_json)
        for f in fields:
            ref_id = f.get("enum_ref_id")
            if ref_id in id_set:
                counts[ref_id] = counts.get(ref_id, 0) + 1
    return counts


async def _find_affected_fields(
    db: AsyncSession, namespace_id: int, enum_id: int,
) -> list[dict]:
    """Find all fields referencing this enum_id."""
    result: list[dict] = []
    scos = (await db.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == namespace_id,
        )
    )).scalars().all()
    for sco in scos:
        if not sco.fields_json:
            continue
        fields = json.loads(sco.fields_json)
        for f in fields:
            if f.get("enum_ref_id") == enum_id:
                result.append({
                    "collection_id": sco.id,
                    "collection_name": sco.target,
                    "field": f.get("name", ""),
                })
    return result


async def _find_reference_items(
    db: AsyncSession, namespace_id: int, enum_id: int,
) -> list[_ReferenceItem]:
    """Reverse scan fields_json for enum_ref_id == enum_id."""
    items: list[_ReferenceItem] = []
    scos = (await db.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == namespace_id,
        )
    )).scalars().all()
    for sco in scos:
        if not sco.fields_json:
            continue
        fields = json.loads(sco.fields_json)
        for f in fields:
            if f.get("enum_ref_id") == enum_id:
                items.append(_ReferenceItem(
                    collection_id=sco.id,
                    collection_name=sco.target,
                    field=f.get("name", ""),
                    field_type=f.get("type"),
                    enum_match_status=f.get("enum_match_status"),
                ))
    return items


def _make_confirm_token(enum_id: int, affected_count: int) -> str:
    raw = f"delete_enum_{enum_id}_{affected_count}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def _try_enqueue_sync(
    db: AsyncSession, enum_dict_id: int, namespace_id: int, event: str,
) -> None:
    """Enqueue EnumSyncQueue row for background worker processing."""
    from app.models.enum_sync_queue import EnumSyncQueue
    db.add(EnumSyncQueue(
        enum_dict_id=enum_dict_id,
        namespace_id=namespace_id,
        event=event,
    ))
    await db.commit()
