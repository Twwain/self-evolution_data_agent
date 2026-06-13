"""Stage 3 Task 10 — 端到端验收: 完整审核闭环 e2e.

闭环路径:
    1. propose (manual create_knowledge 路径直建 proposed)
    2. /audit/queue 命中
    3. POST /audit/{id}/approve → canonical + ChromaDB upsert
    4. _retrieve_layer3 命中 (RAG 入闸)
    5. PUT /knowledge/{id} edit content (走 audit_log + 编辑后冲突)
    6. POST /audit/conflict-preview (LLM patch 桩, 不真调外部)
    7. DELETE /knowledge/{id}?mode=soft → status=rejected + ChromaDB miss
    8. POST /knowledge/{id}/restore → canonical + ChromaDB 恢复命中
    9. GET /audit/{id}/log 时间线 ≥ 4 条 (approve / edit / reject / restore)
"""

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.metadata import get_db
from app.knowledge.intake import ConflictReport
from app.knowledge.knowledge_retriever import _retrieve_layer3
from app.main import app
from app.models import KnowledgeEntry, Namespace
from app.models.user import User


# ─────────────────────────────────────────────────────────────────
# Fixtures (沿 audit/ 既有 isolation pattern, 不复用以保证测试间隔离)
# ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="admin_stage3_e2e",
        password_hash="x",
        role="super_admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def http_client(
    db_session: AsyncSession, admin_user: User
) -> AsyncGenerator[AsyncClient, None]:
    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def ns(db_session: AsyncSession) -> Namespace:
    n = Namespace(name="loop", slug="loop", description="")
    db_session.add(n)
    await db_session.commit()
    await db_session.refresh(n)
    return n


# ─────────────────────────────────────────────────────────────────
# E2E 用例
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_audit_loop_propose_approve_edit_delete_restore(
    db_session: AsyncSession,
    http_client: AsyncClient,
    admin_user: User,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """完整审核闭环 — 写入治理 → 队列 → 通过 → 编辑 → 软删 → 恢复 全链路 sanity."""

    # ── 1. propose: 直建 proposed entry (模拟 user manual /api/knowledge POST 落 proposed) ──
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="老师",
        source="manual",
        status="proposed",
        tier="normal",
        payload='{"term":"老师","synonyms":["teacher"],"primary_database":"test_db","primary_collection":"t_teacher","db_type":"mysql"}',
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)
    eid = entry.id

    # ── 2. /audit/queue 命中 ──
    r = await http_client.get(f"/api/knowledge/audit/queue?namespace_id={ns.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    assert any(item["id"] == eid for item in body["items"]), (
        f"audit queue 未含新 propose entry id={eid}, got={body}"
    )

    # ── 3. approve → canonical ──
    r = await http_client.post(
        f"/api/knowledge/audit/{eid}/approve",
        json={"reason": "通过"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "canonical"

    # ── 4. _retrieve_layer3 命中 (sync 函数包 to_thread) ──
    hits = await asyncio.to_thread(_retrieve_layer3, ns.slug, "老师")
    assert any(h.entry_id == eid for h in hits), (
        f"approve 后 _retrieve_layer3 未命中 entry_id={eid}, hits={hits}"
    )

    # ── 5. PUT edit content (走 audit_log; 同时 patch LLM 跳过编辑后冲突 LLM 调) ──
    with patch(
        "app.knowledge.audit.detect_conflicts",
        return_value=ConflictReport(items=[]),
    ):
        r = await http_client.put(
            f"/api/knowledge/{eid}",
            json={
                "content": "老师=teacher 表 t_teacher (更新)",
                "reason": "补充",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entry"]["content"].endswith("(更新)")
    assert "conflicts" in body  # PUT 响应必含 conflicts 字段

    # ── 6. conflict-preview (patch LLM, 不真调) ──
    with patch(
        "app.knowledge.audit.detect_conflicts",
        return_value=ConflictReport(items=[]),
    ):
        r = await http_client.post(
            "/api/knowledge/audit/conflict-preview",
            json={
                "namespace_id": ns.id,
                "entry_type": "terminology",
                "content": "完全独立的新概念",
                "entry_id": eid,
            },
        )
    assert r.status_code == 200, r.text
    assert "conflicts" in r.json()

    # ── 7. DELETE soft → status=rejected, ChromaDB miss ──
    r = await http_client.delete(
        f"/api/knowledge/{eid}?mode=soft&reason=测试软删"
    )
    assert r.status_code == 204, r.text
    await db_session.refresh(entry)
    assert entry.status == "rejected"

    hits = await asyncio.to_thread(_retrieve_layer3, ns.slug, "老师")
    assert not any(h.entry_id == eid for h in hits), (
        f"soft delete 后 ChromaDB 仍命中 entry_id={eid}, hits={hits}"
    )

    # ── 8. restore → canonical, ChromaDB upsert 恢复命中 ──
    r = await http_client.post(
        f"/api/knowledge/{eid}/restore",
        json={"reason": "误删恢复"},
    )
    assert r.status_code == 200, r.text
    await db_session.refresh(entry)
    assert entry.status == "canonical"

    hits = await asyncio.to_thread(_retrieve_layer3, ns.slug, "老师")
    assert any(h.entry_id == eid for h in hits), (
        f"restore 后 _retrieve_layer3 未恢复命中 entry_id={eid}, hits={hits}"
    )

    # ── 9. /audit/{id}/log 时间线: approve + edit + reject + restore ≥ 4 条 ──
    r = await http_client.get(f"/api/knowledge/audit/{eid}/log")
    assert r.status_code == 200, r.text
    logs = r.json()
    actions = [row["action"] for row in logs]
    for expected in ("approve", "edit", "reject", "restore"):
        assert expected in actions, (
            f"audit_log 缺 action={expected} got={actions}"
        )
