"""code 抽取 → EnumDictionary upsert.

manual 行不被 code 覆盖 (manual 优先).
设计: docs/superpowers/specs/2026-05-18-enum-knowledge-binding/03-enum-dictionary.md §3.1
"""
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.enum_extractor import EnumDef
from app.models.enum_dictionary import EnumDictionary

log = logging.getLogger(__name__)


async def upsert_enum_dictionary_from_code(
    db: AsyncSession,
    namespace_id: int,
    enum_def: EnumDef,
) -> int:
    """code 抽取产物 upsert. 返回 EnumDictionary.id.

    - manual 行 → 跳过, 不覆盖人工编辑
    - code 行 → 更新 values_json + fully_qualified_name
    - 不存在 → 新建 source='code'
    """
    existing = (
        await db.execute(
            select(EnumDictionary).where(
                EnumDictionary.namespace_id == namespace_id,
                EnumDictionary.enum_class_name == enum_def.enum_class,
            )
        )
    ).scalar_one_or_none()

    values_payload = [
        {"name": v.name, "db_value": v.db_value, "description": v.description}
        for v in enum_def.values
    ]
    values_json = json.dumps(values_payload, ensure_ascii=False)

    if existing:
        if existing.source == "manual":
            log.info(
                "[enum_dict] code candidate skipped, manual entry kept "
                "(id=%d, enum_class=%s, updated_at=%s)",
                existing.id,
                enum_def.enum_class,
                existing.updated_at,
            )
            return existing.id
        # source='code' → update
        existing.values_json = values_json
        existing.fully_qualified_name = enum_def.fully_qualified_name
        return existing.id

    new_row = EnumDictionary(
        namespace_id=namespace_id,
        enum_class_name=enum_def.enum_class,
        fully_qualified_name=enum_def.fully_qualified_name,
        values_json=values_json,
        source="code",
    )
    db.add(new_row)
    await db.flush()
    return new_row.id
