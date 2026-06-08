"""
KnowledgeEntry 编辑/下线 API — PATCH + supersede 契约测试

覆盖:
- PATCH content (normal) → upsert 触发
- PATCH tier normal → critical → delete 触发 (critical 不入池)
- PATCH tier critical → normal → upsert 触发
- POST supersede → status=superseded + delete 触发
- 404 条目不存在
"""

from unittest.mock import patch

import pytest

from app.models import KnowledgeEntry, Namespace


# ─────────────────────────── fixture ───────────────────────────

async def _mk_entry(db, ns_id: int, tier: str = "normal", status: str = "canonical"):
    """Stage 1: 默认 status=canonical (已审进 RAG), 与旧 reviewed=True 等价语义."""
    e = KnowledgeEntry(
        namespace_id=ns_id,
        entry_type="terminology",
        tier=tier,
        content="原始内容",
        raw_input="用户原文",
        description="摘要",
        source="manual",
        status=status,
    )
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e


# ─────────────────────────── PATCH 测试 ───────────────────────────

@pytest.mark.asyncio
async def test_patch_content_triggers_upsert(db, admin_client):
    """改 content (normal + canonical) → 向量 upsert 刷新"""
    ns = Namespace(name="t2", slug="t2")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)
    entry = await _mk_entry(db, ns.id)

    with patch("app.api.knowledge.upsert_knowledge_entry") as mock_up, \
         patch("app.api.knowledge.delete_knowledge_entry") as mock_del:
        r = await admin_client.patch(
            f"/api/knowledge/{entry.id}", json={"content": "修改后内容"},
        )

    assert r.status_code == 200
    assert r.json()["content"] == "修改后内容"
    mock_up.assert_called_once()
    mock_del.assert_not_called()


@pytest.mark.asyncio
async def test_patch_tier_normal_to_critical_deletes_vector(db, admin_client):
    """tier normal → critical → 从 ChromaDB 删向量 (critical 走 SQL 直取)"""
    ns = Namespace(name="t3", slug="t3")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)
    entry = await _mk_entry(db, ns.id, tier="normal")

    with patch("app.api.knowledge.upsert_knowledge_entry") as mock_up, \
         patch("app.api.knowledge.delete_knowledge_entry") as mock_del:
        r = await admin_client.patch(
            f"/api/knowledge/{entry.id}", json={"tier": "critical"},
        )

    assert r.status_code == 200
    assert r.json()["tier"] == "critical"
    mock_del.assert_called_once()
    mock_up.assert_not_called()


@pytest.mark.asyncio
async def test_patch_tier_critical_to_normal_upserts_vector(db, admin_client):
    """tier critical → normal → 向量入池"""
    ns = Namespace(name="t4", slug="t4")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)
    entry = await _mk_entry(db, ns.id, tier="critical")

    with patch("app.api.knowledge.upsert_knowledge_entry") as mock_up, \
         patch("app.api.knowledge.delete_knowledge_entry") as mock_del:
        r = await admin_client.patch(
            f"/api/knowledge/{entry.id}", json={"tier": "normal"},
        )

    assert r.status_code == 200
    assert r.json()["tier"] == "normal"
    mock_up.assert_called_once()
    mock_del.assert_not_called()


@pytest.mark.asyncio
async def test_patch_404(db, admin_client):
    r = await admin_client.patch("/api/knowledge/99999", json={"status": "canonical"})
    assert r.status_code == 404


# ─────────────────────────── supersede 测试 ───────────────────────────

@pytest.mark.asyncio
async def test_supersede_sets_flag_and_deletes_vector(db, admin_client):
    ns = Namespace(name="t5", slug="t5")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)
    entry = await _mk_entry(db, ns.id)

    with patch("app.api.knowledge.delete_knowledge_entry") as mock_del:
        r = await admin_client.post(f"/api/knowledge/{entry.id}/supersede")

    assert r.status_code == 200
    body = r.json()
    assert body["is_superseded"] is True
    assert body["status"] == "superseded"
    mock_del.assert_called_once()


@pytest.mark.asyncio
async def test_supersede_404(db, admin_client):
    r = await admin_client.post("/api/knowledge/99999/supersede")
    assert r.status_code == 404
