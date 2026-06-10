"""introspect 通道入 candidate 层.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/03-extraction.md §3.8

把 driver introspect 的一张表 detail 拆成多条 candidate (table_description + field_description).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.canonical_candidate import write_canonical_candidate


async def write_introspect_candidates_for_target(
    db: AsyncSession,
    *,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
    detail: dict[str, Any],
    datasource_id: int | None,
) -> int:
    """把 driver introspect 的一张表 detail 拆成多条 candidate.

    返回新增/更新的 candidate 数量.

    detail 来自 MySQLDriver.fetch_schema(target=...), 含:
      - description: 表注释
      - fields: list[dict] 含 name/type/description/nullable/indexed
      - indexes: 索引列表 (Phase 1 不入 candidate, 走 SCO.indexes_json 直接更新)
      - sample_count: 行数估计 (Phase 1 不入 candidate)
    """
    count = 0

    # 表级 description
    table_desc = detail.get("description", "")
    await write_canonical_candidate(
        db,
        namespace_id=namespace_id,
        db_type=db_type,
        database=database,
        target=target,
        field_path="",
        candidate_kind="table_description",
        candidate_value={"description": table_desc},
        evidence_sources=[{
            "source": "introspect",
            "datasource_id": datasource_id,
        }],
        confidence_status="confirmed_by_introspect",
        datasource_id=datasource_id,
    )
    count += 1

    # 字段级 description
    for f in detail.get("fields", []):
        candidate_value: dict = {"description": f.get("description", "")}
        if f.get("type"):
            candidate_value["type"] = f.get("type")
        if f.get("nullable") is not None:
            candidate_value["nullable"] = f.get("nullable")
        if f.get("indexed"):
            candidate_value["indexed"] = True
        await write_canonical_candidate(
            db,
            namespace_id=namespace_id,
            db_type=db_type,
            database=database,
            target=target,
            field_path=f["name"],
            candidate_kind="field_description",
            candidate_value=candidate_value,
            evidence_sources=[{
                "source": "introspect",
                "datasource_id": datasource_id,
                "field_type": f.get("type", ""),
            }],
            confidence_status="confirmed_by_introspect",
            datasource_id=datasource_id,
        )
        count += 1

    # 字段级 enum_values (如有)
    for f in detail.get("fields", []):
        enum_values = f.get("enum_values")
        if enum_values:
            await write_canonical_candidate(
                db,
                namespace_id=namespace_id,
                db_type=db_type,
                database=database,
                target=target,
                field_path=f["name"],
                candidate_kind="enum_values",
                candidate_value={"enum_values": enum_values},
                evidence_sources=[{
                    "source": "introspect",
                    "datasource_id": datasource_id,
                }],
                confidence_status="confirmed_by_introspect",
                datasource_id=datasource_id,
            )
            count += 1

    return count
