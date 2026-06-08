"""Bug condition exploration tests — mybatis_extract cleanup & write filter.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6**

These tests confirm the bugs are FIXED by asserting expected behavior against real PostgreSQL.

Bug 1 (Purge): _delete_legacy_kes now deletes source='mybatis_extract' entries
Bug 2b (Route hints): _write_route_hints skips non-select entries in aggregation
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import DataSource, Namespace
from app.models.git_repo import GitRepo


# ════════════════════════════════════════════════════════════════
#  Fixtures — reuse async_session from knowledge/conftest.py
# ════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def seeded(async_session) -> tuple[int, int, object]:
    """Create namespace + datasource + repo with unique names."""
    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(name=f"bug_{uid}", slug=f"bug_{uid}", description="bug exploration test")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        ds = DataSource(
            namespace_id=ns.id, db_type="mysql", database="test_db",
            host="localhost", port=3306, username="", password="",
        )
        db.add(ds)
        await db.commit()

        repo = GitRepo(namespace_id=ns.id, url=f"https://example.invalid/bug_{uid}.git")
        db.add(repo)
        await db.commit()
        await db.refresh(repo)
        return ns.id, repo.id, async_session


# ════════════════════════════════════════════════════════════════
#  Bug 1: Purge deletes source='mybatis_extract' entries
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@given(
    entry_type=st.sampled_from(["example", "route_hint"]),
    status=st.sampled_from(["proposed", "canonical", "superseded", "rejected"]),
)
@settings(
    max_examples=5,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=None,
)
async def test_bug1_purge_deletes_mybatis_extract_entries(
    seeded: tuple[int, int, object], entry_type: str, status: str,
):
    """Bug 1: _delete_legacy_kes SHALL delete source='mybatis_extract' entries."""
    from app.knowledge.trainer_purge import _delete_legacy_kes

    ns_id, repo_id, session_factory = seeded

    async with session_factory() as db:
        ke = KnowledgeEntry(
            namespace_id=ns_id,
            entry_type=entry_type,
            status=status,
            tier="normal",
            content="mybatis extract test entry",
            payload="{}",
            source="mybatis_extract",
            repo_id=repo_id,
        )
        db.add(ke)
        await db.flush()
        ke_id = ke.id

        deleted = await _delete_legacy_kes(db, repo_id)
        deleted_ids = [row[0] for row in deleted]

        assert ke_id in deleted_ids, (
            f"_delete_legacy_kes should delete source='mybatis_extract' entry with "
            f"entry_type={entry_type!r}, status={status!r}, but it was not deleted."
        )
        await db.rollback()


# ════════════════════════════════════════════════════════════════
#  Bug 2b: _write_route_hints excludes non-select entries
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@given(
    non_select_type=st.sampled_from(["insert", "update", "delete"]),
)
@settings(
    max_examples=3,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=None,
)
async def test_bug2b_write_route_hints_excludes_non_select(
    seeded: tuple[int, int, object], non_select_type: str,
):
    """Bug 2b: _write_route_hints SHALL skip non-select entries in aggregation."""
    from app.knowledge.extraction_writer import _write_route_hints
    from sqlalchemy import select as sa_select

    ns_id, repo_id, session_factory = seeded

    sql_templates = {
        "insert": "INSERT INTO t_non_select_target (col1) VALUES (?)",
        "update": "UPDATE t_non_select_target SET col1 = ? WHERE id = ?",
        "delete": "DELETE FROM t_non_select_target WHERE id = ?",
    }

    mybatis_entries = [
        {
            "type": non_select_type,
            "canonical_sql": sql_templates[non_select_type],
            "mapper_namespace": "com.example.TestMapper",
            "method_id": f"{non_select_type}Record",
        },
        {
            "type": "select",
            "canonical_sql": "SELECT * FROM t_allowed_table WHERE id = ?",
            "mapper_namespace": "com.example.TestMapper",
            "method_id": "selectById",
        },
    ]

    async with session_factory() as db:
        count = await _write_route_hints(db, ns_id, repo_id, mybatis_entries)

        # Check what was written — query KE with entry_type='route_hint'
        rows = (await db.execute(
            sa_select(KnowledgeEntry.content).where(
                KnowledgeEntry.namespace_id == ns_id,
                KnowledgeEntry.repo_id == repo_id,
                KnowledgeEntry.entry_type == "route_hint",
            )
        )).scalars().all()

        for content in rows:
            assert "t_non_select_target" not in content, (
                f"_write_route_hints included table from {non_select_type.upper()} SQL "
                f"in route_hint content: '{content}'. "
                f"Non-select entries should be excluded from aggregation."
            )
        await db.rollback()
