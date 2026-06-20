"""Schema Canonical REST API — 通用 schema 真相源管理.

端点:
- GET  /api/namespaces/{ns_id}/schema-canonical          列出全部 (可选 ?db_type=mysql)
- GET  /api/namespaces/{ns_id}/schema-canonical/{id}     单条详情
- PATCH /api/namespaces/{ns_id}/schema-canonical/{id}    编辑 (fields description / description)
- POST /api/namespaces/{ns_id}/schema-canonical/refresh  触发 MySQL introspect 刷新
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_ns_manage
from app.db.metadata import get_db
from app.knowledge.schema_canonical import (
    get_schema_canonical,
    list_schema_canonicals,
    refresh_mysql_canonicals,
    upsert_schema_canonical,
)
from app.models import SchemaCanonicalObject
from app.models.user import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/namespaces/{ns_id}/schema-canonical", tags=["schema-canonical"])


# ── Response schemas ──

class SchemaCanonicalOut(BaseModel):
    id: int
    namespace_id: int
    db_type: str
    database: str
    target: str
    fields: list[dict]
    indexes: list[dict]
    description: str
    purpose_detail: str
    sample_count: int
    source: str
    relationships: list[dict] = []
    user_locked: bool = False

    @classmethod
    def from_orm(cls, obj: SchemaCanonicalObject) -> "SchemaCanonicalOut":
        try:
            fields = json.loads(obj.fields_json or "[]")
        except json.JSONDecodeError:
            fields = []
        try:
            indexes = json.loads(obj.indexes_json or "[]")
        except json.JSONDecodeError:
            indexes = []
        try:
            relationships = json.loads(obj.relationships_json or "[]")
        except json.JSONDecodeError:
            relationships = []
        return cls(
            id=obj.id,
            namespace_id=obj.namespace_id,
            db_type=obj.db_type,
            database=obj.database,
            target=obj.target,
            fields=fields,
            indexes=indexes,
            description=obj.description or "",
            purpose_detail=obj.purpose_detail or "",
            sample_count=obj.sample_count,
            source=obj.source or "introspect",
            relationships=relationships,
            user_locked=obj.user_locked,
        )


class SchemaCanonicalPatch(BaseModel):
    description: str | None = None
    purpose_detail: str | None = None
    fields: list[dict] | None = None  # 用户编辑字段 description


# ── Endpoints ──

@router.get("")
async def list_canonicals(
    ns_id: int,
    db_type: str | None = Query(None),
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> list[SchemaCanonicalOut]:
    """列出 namespace 下全部 schema canonical."""
    rows = await list_schema_canonicals(db, ns_id, db_type=db_type)
    return [SchemaCanonicalOut.from_orm(r) for r in rows]


@router.get("/{sco_id}")
async def get_canonical(
    ns_id: int,
    sco_id: int,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> SchemaCanonicalOut:
    """获取单条 schema canonical 详情."""
    obj = await db.get(SchemaCanonicalObject, sco_id)
    if not obj or obj.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")
    return SchemaCanonicalOut.from_orm(obj)


@router.patch("/{sco_id}")
async def patch_canonical(
    ns_id: int,
    sco_id: int,
    body: SchemaCanonicalPatch,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> SchemaCanonicalOut:
    """编辑 schema canonical (description / fields description).

    修订 #3: 任何 PATCH 都自动标 user_locked=True, 防止下次 promote 覆盖人工编辑.
    """
    from app.knowledge.canonical_audit import write_canonical_audit_log

    obj = await db.get(SchemaCanonicalObject, sco_id)
    if not obj or obj.namespace_id != ns_id:
        raise HTTPException(404, "schema canonical not found")

    if body.description is not None:
        obj.description = body.description
    if body.purpose_detail is not None:
        obj.purpose_detail = body.purpose_detail
    if body.fields is not None:
        obj.fields_json = json.dumps(body.fields, ensure_ascii=False)

    # 修订 #3: auto-lock on patch
    obj.user_locked = True
    obj.updated_at = datetime.now()

    await write_canonical_audit_log(
        db, namespace_id=ns_id, action="user_lock",
        canonical_id=sco_id, actor_id=user.id,
        reason="auto_lock_on_patch",
    )
    await db.commit()
    await db.refresh(obj)
    return SchemaCanonicalOut.from_orm(obj)


@router.post("/refresh")
async def refresh_canonicals(
    ns_id: int,
    user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """触发 MySQL introspect 刷新 (重新从 INFORMATION_SCHEMA 拉取)."""
    from app.models import Namespace
    ns = await db.get(Namespace, ns_id)
    if not ns:
        raise HTTPException(404, "namespace not found")

    count = await refresh_mysql_canonicals(db, ns_id, ns.slug)
    await db.commit()
    return {"refreshed": count, "namespace_id": ns_id}
