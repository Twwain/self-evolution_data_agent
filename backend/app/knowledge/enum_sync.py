"""EnumDictionary 变更 → 字段 inline snapshot 反向同步.

create 事件含启发式 rebind: 复用 _resolve_enum_class Layer 4 词根匹配,
覆盖"pending 字段无 hint, EnumDictionary 后到"场景.

设计: docs/superpowers/specs/2026-05-18-enum-knowledge-binding/04-field-enum-binding.md §4
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.knowledge.canonical_audit import write_canonical_audit_log
from app.knowledge.enum_extractor import (
    ENUM_NAME_SUFFIXES,
    EnumDef,
    EnumValue,
    _resolve_enum_class,
    _split_camel,
)
from app.models.enum_binding_conflict import EnumBindingConflict
from app.models.enum_dictionary import EnumDictionary
from app.models.enum_sync_queue import EnumSyncQueue
from app.models.schema_canonical_object import SchemaCanonicalObject

logger = logging.getLogger(__name__)

EnumSyncEvent = Literal["create", "update", "delete"]


async def sync_enum_dict_to_bound_fields(
    db: AsyncSession,
    enum_dict_id: int,
    *,
    event: EnumSyncEvent,
    namespace_id: int | None = None,
) -> dict[str, Any]:
    """三事件分发. 返回 {rebound|synced|unbound, conflicts?}.

    delete 事件 EnumDictionary 已不存在, namespace_id 必须由调用方提供.
    """
    if event == "delete":
        if namespace_id is None:
            return {"status": "skipped", "reason": "delete requires namespace_id"}
        return await _handle_delete(db, enum_dict_id, namespace_id)

    e = (await db.execute(
        select(EnumDictionary).where(EnumDictionary.id == enum_dict_id)
    )).scalar_one_or_none()
    if not e:
        return {"status": "skipped", "reason": "enum not found"}

    if event == "create":
        return await _handle_create(db, e)
    if event == "update":
        return await _handle_update(db, e)
    return {"status": "unknown_event"}


# ════════════════════════════════════════════
#  Internal helpers
# ════════════════════════════════════════════


def _enum_to_def(e: EnumDictionary) -> EnumDef:
    """EnumDictionary → EnumDef (供启发式复用)."""
    values = [
        EnumValue(
            name=v["name"],
            db_value=v["db_value"],
            description=v.get("description"),
        )
        for v in json.loads(e.values_json)
    ]
    return EnumDef(
        enum_class=e.enum_class_name,
        fully_qualified_name=e.fully_qualified_name or e.enum_class_name,
        values=values,
    )


async def _resolve_open_conflicts(
    db: AsyncSession,
    *,
    field_canonical_id: int,
    field_name: str,
    enum_dict_id: int,
    resolver_id: int | None,
) -> int:
    """关闭 (field, enum) 上仍 open 的 EnumBindingConflict 行 — sample 已不再
    冲突或字段已 unbind 时调用. 返回被关闭的行数, 跨行复用 partial unique 防重.

    为什么需要: 状态机不闭环会让 UI"待解决冲突"列表残留已 resolved 的孤儿,
    且 conflict 行会持续阻塞 partial unique 防重路径的语义.
    """
    open_rows = (await db.execute(
        select(EnumBindingConflict).where(
            EnumBindingConflict.field_canonical_id == field_canonical_id,
            EnumBindingConflict.field_name == field_name,
            EnumBindingConflict.enum_dict_id == enum_dict_id,
            EnumBindingConflict.status == "open",
        )
    )).scalars().all()
    now = datetime.now()
    for c in open_rows:
        c.status = "resolved"
        c.resolved_at = now
        c.resolved_by = resolver_id
    return len(open_rows)


async def _handle_create(db: AsyncSession, e: EnumDictionary) -> dict[str, Any]:
    """新增 EnumDictionary → 扫 pending 字段, hint 匹配 + 启发式 rebind."""
    enum_def = _enum_to_def(e)
    enum_class_index = {e.enum_class_name: enum_def}
    enum_values_payload = json.loads(e.values_json)

    rows = (await db.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == e.namespace_id,
        )
    )).scalars().all()

    rebound = 0
    for sco in rows:
        try:
            fields = json.loads(sco.fields_json or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        changed = False
        for f in fields:
            if f.get("enum_match_status") != "pending":
                continue

            # Layer 1: hint 直接匹配
            bind_source: str | None = None
            if f.get("enum_class_hint") == e.enum_class_name:
                bind_source = "code_hint"
            else:
                # Layer 4: 启发式词根匹配
                ec, src = _resolve_enum_class(f, enum_class_index)
                if ec == e.enum_class_name:
                    bind_source = src

            if bind_source:
                f["enum_ref_id"] = e.id
                f["enum_values"] = enum_values_payload
                f["enum_source"] = bind_source
                f["enum_match_status"] = "matched"
                changed = True
                rebound += 1

        if changed:
            sco.fields_json = json.dumps(fields, ensure_ascii=False)
            await write_canonical_audit_log(
                db,
                namespace_id=e.namespace_id,
                action="enum_dict_auto_rebind",
                canonical_id=sco.id,
                reason=f"create event for enum_dict_id={e.id} ({e.enum_class_name})",
            )

    await db.commit()
    return {"rebound": rebound}


async def _handle_update(db: AsyncSession, e: EnumDictionary) -> dict[str, Any]:
    """EnumDictionary values 改 → 刷 snapshot, sample 不覆盖时进 conflict."""
    enum_values_payload = json.loads(e.values_json)
    enum_db_values = {v["db_value"] for v in enum_values_payload}

    rows = (await db.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == e.namespace_id,
        )
    )).scalars().all()

    synced = 0
    conflicts = 0
    for sco in rows:
        try:
            fields = json.loads(sco.fields_json or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        changed = False
        for f in fields:
            if f.get("enum_ref_id") != e.id:
                continue
            f["enum_values"] = enum_values_payload
            sample = f.get("sample_values") or []
            if sample:
                not_covered = [s for s in sample if s not in enum_db_values]
                if not_covered:
                    f["enum_match_status"] = "conflict"
                    # 写 EnumBindingConflict (partial unique 防重)
                    existing = (await db.execute(
                        select(EnumBindingConflict).where(
                            EnumBindingConflict.field_canonical_id == sco.id,
                            EnumBindingConflict.field_name == (f.get("name") or ""),
                            EnumBindingConflict.enum_dict_id == e.id,
                            EnumBindingConflict.status == "open",
                        )
                    )).scalar_one_or_none()
                    if not existing:
                        db.add(EnumBindingConflict(
                            namespace_id=e.namespace_id,
                            field_canonical_id=sco.id,
                            field_name=f.get("name") or "",
                            enum_dict_id=e.id,
                            conflict_kind="value_not_covered",
                            detail_json=json.dumps({
                                "sample": sample,
                                "not_covered": not_covered,
                            }),
                            status="open",
                        ))
                    conflicts += 1
                else:
                    f["enum_match_status"] = "matched"
                    # sample 已全覆盖 → resolve 任何挂着的 open conflict
                    await _resolve_open_conflicts(
                        db,
                        field_canonical_id=sco.id,
                        field_name=f.get("name") or "",
                        enum_dict_id=e.id,
                        resolver_id=e.updated_by,
                    )
            else:
                # 无 sample, 仅刷 snapshot, 状态保持 matched
                f["enum_match_status"] = "matched"
                # 无 sample 视同语义满足 → 同样关闭旧 open conflict
                await _resolve_open_conflicts(
                    db,
                    field_canonical_id=sco.id,
                    field_name=f.get("name") or "",
                    enum_dict_id=e.id,
                    resolver_id=e.updated_by,
                )
            changed = True
            synced += 1

        if changed:
            sco.fields_json = json.dumps(fields, ensure_ascii=False)
            await write_canonical_audit_log(
                db,
                namespace_id=e.namespace_id,
                action="enum_dict_value_sync",
                canonical_id=sco.id,
                actor_id=e.updated_by,
                reason=f"update event for enum_dict_id={e.id}",
            )

    await db.commit()
    return {"synced": synced, "conflicts": conflicts}


async def _handle_delete(
    db: AsyncSession, enum_dict_id: int, namespace_id: int,
) -> dict[str, Any]:
    """EnumDictionary 删除 → cascade unbind 该 namespace 内所有引用字段."""
    rows = (await db.execute(
        select(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == namespace_id,
        )
    )).scalars().all()

    unbound = 0
    for sco in rows:
        try:
            fields = json.loads(sco.fields_json or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        changed = False
        for f in fields:
            if f.get("enum_ref_id") != enum_dict_id:
                continue
            for k in ("enum_ref_id", "enum_values", "enum_source"):
                f.pop(k, None)
            fname = f.get("name") or ""
            tokens = _split_camel(fname)
            has_suffix = (
                len(tokens) >= 2
                and (tokens[-1][:1].upper() + tokens[-1][1:]) in ENUM_NAME_SUFFIXES
            )
            if has_suffix:
                f["enum_match_status"] = "pending"
            else:
                f.pop("enum_match_status", None)
            # cascade unbind 后 → 关闭该 (field, enum) 仍开着的 conflict
            await _resolve_open_conflicts(
                db,
                field_canonical_id=sco.id,
                field_name=fname,
                enum_dict_id=enum_dict_id,
                resolver_id=None,
            )
            changed = True
            unbound += 1

        if changed:
            sco.fields_json = json.dumps(fields, ensure_ascii=False)
            await write_canonical_audit_log(
                db,
                namespace_id=namespace_id,
                action="enum_dict_unbind_cascade",
                canonical_id=sco.id,
                reason=f"delete event for enum_dict_id={enum_dict_id}",
            )

    await db.commit()
    return {"unbound": unbound}


# ════════════════════════════════════════════
#  Background worker
# ════════════════════════════════════════════


async def enum_sync_loop_once(db: AsyncSession) -> int:
    """处理 queue 一轮, 返回任务数. 单条失败写 audit 不阻塞.

    Dedup: 同 (enum_dict_id, event) 短时间多次入队时, 只跑最新一条 (按 created_at),
    其余直接丢弃 — 避免连续编辑 EnumDictionary 导致同 enum 被反复扫 N 次, 重复写
    audit_log. 不影响正确性: sync_enum_dict_to_bound_fields 是幂等的, 只跑最新
    一条已能让字段 inline snapshot 反映最终态.
    """
    rows = list((await db.execute(
        select(EnumSyncQueue)
        .order_by(EnumSyncQueue.created_at)
        .limit(settings.enum_sync_batch_size)
    )).scalars().all())

    # 折叠: (enum_dict_id, event) 仅保留 created_at 最新一条 (取顺序最后);
    # 其余 stale 任务一并删除, 但不再触发 sync 回调.
    keep_by_key: dict[tuple[int, str], EnumSyncQueue] = {}
    stale_rows: list[EnumSyncQueue] = []
    for row in rows:
        key = (row.enum_dict_id, row.event)
        prior = keep_by_key.get(key)
        if prior is not None:
            stale_rows.append(prior)
        keep_by_key[key] = row

    processed = 0
    for stale in stale_rows:
        await db.delete(stale)
        processed += 1

    for row in keep_by_key.values():
        try:
            await sync_enum_dict_to_bound_fields(
                db, row.enum_dict_id,
                event=row.event,  # type: ignore[arg-type]
                namespace_id=row.namespace_id,
            )
        except Exception as exc:
            logger.exception(
                "enum_sync task failed: id=%d event=%s", row.id, row.event,
            )
            try:
                await write_canonical_audit_log(
                    db,
                    namespace_id=row.namespace_id,
                    action="enum_sync_failed",
                    reason=(
                        f"event={row.event} error={type(exc).__name__}: "
                        f"{str(exc)[:200]}"
                    ),
                )
            except Exception:
                pass
        await db.delete(row)
        await db.commit()
        processed += 1
    return processed


async def enum_sync_loop(db_factory) -> None:  # type: ignore[type-arg]
    """常驻后台任务. 异常隔离, CancelledError 正常退出."""
    import asyncio

    while True:
        try:
            async with db_factory() as db:
                await enum_sync_loop_once(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("enum_sync_loop tick failed")
        await asyncio.sleep(settings.enum_sync_interval_secs)
