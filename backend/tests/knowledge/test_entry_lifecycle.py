"""KnowledgeEntry 模型层测试 — 真实 SQLite, 不涉及 ChromaDB."""

import pytest

from app.models.knowledge_entry import KnowledgeEntry


@pytest.mark.asyncio
async def test_create_entry_with_payload(db_session):
    entry = KnowledgeEntry(
        namespace_id=None,
        entry_type="terminology",
        status="proposed",
        tier="normal",
        content="条目",
        payload='{"term": "条目"}',
        source="manual",
    )
    db_session.add(entry)
    await db_session.commit()
    assert entry.id is not None
    assert entry.status == "proposed"
    assert entry.evidence_json == "{}"
    assert entry.superseded_by is None
    assert entry.reviewed_by_id is None


@pytest.mark.asyncio
async def test_status_default_proposed(db_session):
    entry = KnowledgeEntry(entry_type="rule", content="x", source="manual")
    db_session.add(entry)
    await db_session.commit()
    assert entry.status == "proposed"
    assert entry.tier == "normal"


@pytest.mark.asyncio
async def test_superseded_by_self_reference(db_session):
    old = KnowledgeEntry(entry_type="terminology", content="old", source="manual")
    db_session.add(old)
    await db_session.flush()
    new = KnowledgeEntry(entry_type="terminology", content="new", source="manual")
    db_session.add(new)
    await db_session.flush()

    old.status = "superseded"
    old.superseded_by = new.id
    old.is_superseded = True
    await db_session.commit()

    assert old.superseded_by == new.id
