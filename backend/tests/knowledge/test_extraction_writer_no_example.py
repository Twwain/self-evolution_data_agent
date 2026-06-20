"""验证 mybatis_entries 产 route_hint KE.

example 通道由 sql2nl 经 business_examples 恢复 (D3, 见 spec
2026-06-17-agentic-repo-extractor Task 2.1) — mybatis_entries 本身不产 example,
example 由 agent emit_knowledge(entry_type=example) → business_examples 通道写入。
本文件仅保留 route_hint 回归保护。

原 test_mybatis_entries_do_not_produce_example_ke 已删除: D3 恢复 example 后
"mybatis 不产 example" 的断言语义已不再适用于整体管线 (sql2nl 经独立通道产 example)。
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.knowledge.extraction_writer import extract_and_write_knowledge
from app.models import KnowledgeEntry
from app.models.git_repo import GitRepo
from app.models.namespace import Namespace


@pytest_asyncio.fixture
async def seeded(db_session) -> tuple[int, int]:
    """Create namespace + repo, return (ns_id, repo_id)."""
    ns = Namespace(name="test_ne", slug="test_ne", description="no-example test")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    repo = GitRepo(namespace_id=ns.id, url="https://example.invalid/ne.git")
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return ns.id, repo.id


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
