"""Stage A — 验证 mybatis_entries 不再产 entry_type=example KE.

Spec: docs/superpowers/specs/2026-05-25-mysql-canonical-pull-and-mybatis-example-deprecation
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.knowledge.extraction_writer import extract_and_write_knowledge
from app.models import KnowledgeEntry


@pytest.mark.asyncio
async def test_mybatis_entries_do_not_produce_example_ke(db_session, seeded):
    """喂典型 mybatis SELECT entry → entry_type=example KE 数为 0."""
    ns_id, repo_id = seeded
    mybatis_entries = [
        {
            "type": "select",
            "method_id": "selectUserById",
            "mapper_namespace": "com.example.UserMapper",
            "canonical_sql": "SELECT user_id, real_name FROM t_user WHERE user_id = ?",
            "table_name": "t_user",
        },
        {
            "type": "select",
            "method_id": "selectUserByName",
            "mapper_namespace": "com.example.UserMapper",
            "canonical_sql": "SELECT * FROM t_user WHERE user_name = ?",
            "table_name": "t_user",
        },
    ]

    await extract_and_write_knowledge(
        db_session,
        namespace_id=ns_id,
        repo_id=repo_id,
        mybatis_entries=mybatis_entries,
        business_terms=[],
        business_rules=[],
    )
    await db_session.commit()

    rows = list((await db_session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.repo_id == repo_id,
            KnowledgeEntry.entry_type == "example",
        )
    )).scalars().all())
    assert rows == [], (
        f"Stage A 要求 mybatis 路径不再产 example, 实际产生 {len(rows)} 条: "
        f"{[r.content for r in rows[:3]]}"
    )


@pytest.mark.asyncio
async def test_mybatis_entries_still_produce_route_hint_ke(db_session, seeded):
    """合法出口仍工作: route_hint KE 仍然产出 (回归保护)."""
    ns_id, repo_id = seeded
    mybatis_entries = [
        {
            "type": "select",
            "method_id": "selectUserById",
            "mapper_namespace": "com.example.UserMapper",
            "canonical_sql": "SELECT user_id, real_name FROM t_user WHERE user_id = ?",
        },
    ]

    await extract_and_write_knowledge(
        db_session,
        namespace_id=ns_id,
        repo_id=repo_id,
        mybatis_entries=mybatis_entries,
        business_terms=[],
        business_rules=[],
    )
    await db_session.commit()

    rows = list((await db_session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.repo_id == repo_id,
            KnowledgeEntry.entry_type == "route_hint",
        )
    )).scalars().all())
    assert len(rows) >= 1, "route_hint 出口被误伤"
