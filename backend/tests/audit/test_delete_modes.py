"""Stage 3 Task 6 — DELETE /api/knowledge/{id} mode 升级.

5 用例覆盖:
    1. soft + proposed → 物理删 + audit_log(reject from=proposed to=rejected)
    2. soft + canonical → status=rejected + ChromaDB delete + audit_log(reject)
    3. soft + 终态 (superseded/rejected) → 422 already_terminal_state
    4. hard + 默认 settings.knowledge_hard_delete_enabled=False → 403
    5. hard + 启用 → 物理删 + ChromaDB delete + audit_log(hard_delete)
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.metadata import get_db
from app.knowledge.knowledge_retriever import upsert_knowledge_entry
from app.main import app
from app.models import KnowledgeEntry, Namespace
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.user import User


# ────────────────────────────────────────────────────���────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="admin_delete_modes",
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
    n = Namespace(name="dm_ns", slug="dm_ns", description="")
    db_session.add(n)
    await db_session.commit()
    await db_session.refresh(n)
    return n


def _chromadb_count(slug: str) -> int:
    from app.engine.embedding import get_embedding_function
    from app.engine.registry import get_chroma_client

    coll = get_chroma_client().get_collection(
        f"ns_{slug}_knowledge",
        embedding_function=get_embedding_function(),  # type: ignore[arg-type]
    )
    return coll.count()


# ───────────────────────────────────────���─────────────────────────
# 测试用例
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_delete_proposed_physically_removes_with_audit(
    http_client: AsyncClient,
    db_session: AsyncSession,
    admin_user: User,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """proposed + soft → 物理删 (proposed 无价值保留), audit_log 留时间线痕迹."""
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        content="待删",
        source="manual",
        status="proposed",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)
    entry_id = entry.id

    r = await http_client.delete(
        f"/api/knowledge/{entry_id}?mode=soft&reason=不要"
    )
    assert r.status_code == 204, r.text

    # ── KE 表中已无 ──
    remaining = await db_session.get(KnowledgeEntry, entry_id)
    assert remaining is None

    # ── audit_log 1 条, action=reject from=proposed to=rejected reason=不要 ──
    logs = (await db_session.scalars(
        select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == entry_id)
    )).all()
    assert len(logs) == 1
    assert logs[0].action == "reject"
    assert logs[0].from_status == "proposed"
    assert logs[0].to_status == "rejected"
    assert logs[0].reason == "不要"
    assert logs[0].actor_id == admin_user.id


@pytest.mark.asyncio
async def test_soft_delete_canonical_marks_rejected_and_chromadb_delete(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """canonical + soft → status=rejected, ChromaDB 清向量."""
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="待软删",
        source="manual",
        status="canonical",
        tier="normal",
        payload='{"term":"待软删","synonyms":[],"primary_database":"test_db","primary_collection":"test_coll","db_type":"mongodb"}',
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    from app.knowledge.knowledge_retriever import parse_entry_payload
    await asyncio.to_thread(
        upsert_knowledge_entry,
        slug=ns.slug,
        entry_id=entry.id,
        content=entry.content,
        tier=entry.tier,
        namespace_id=entry.namespace_id,
        entry_type=entry.entry_type,
        status=entry.status,
        payload=parse_entry_payload(entry.payload),
    )
    assert _chromadb_count(ns.slug) == 1

    r = await http_client.delete(
        f"/api/knowledge/{entry.id}?mode=soft&reason=过期"
    )
    assert r.status_code == 204, r.text

    await db_session.refresh(entry)
    assert entry.status == "rejected"
    assert _chromadb_count(ns.slug) == 0

    logs = (await db_session.scalars(
        select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == entry.id)
    )).all()
    assert len(logs) == 1
    assert logs[0].action == "reject"
    assert logs[0].from_status == "canonical"
    assert logs[0].to_status == "rejected"
    assert logs[0].reason == "过期"


@pytest.mark.asyncio
async def test_soft_delete_terminal_state_returns_422(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """superseded / rejected 是终态, soft 删拒绝 422 already_terminal_state."""
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        content="已被替代",
        source="manual",
        status="superseded",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await http_client.delete(
        f"/api/knowledge/{entry.id}?mode=soft&reason=再删一次"
    )
    assert r.status_code == 422, r.text
    assert "already_terminal_state" in r.text


@pytest.mark.asyncio
async def test_hard_delete_disabled_returns_403(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    """默认 settings.knowledge_hard_delete_enabled=False, hard 模式被拒 403."""
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        content="试图硬删",
        source="manual",
        status="canonical",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await http_client.delete(
        f"/api/knowledge/{entry.id}?mode=hard&reason=必须删"
    )
    assert r.status_code == 403, r.text
    assert "hard_delete_disabled" in r.text


@pytest.mark.asyncio
async def test_hard_delete_enabled_physically_removes_with_audit(
    http_client: AsyncClient,
    db_session: AsyncSession,
    admin_user: User,
    ns: Namespace,
    chroma_isolated,
    monkeypatch,
) -> None:
    """启用 IS_KNOWLEDGE_HARD_DELETE_ENABLED → 物理删 + ChromaDB 清 + audit hard_delete."""
    monkeypatch.setattr(
        "app.config.settings.knowledge_hard_delete_enabled", True
    )

    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="hard 删",
        source="manual",
        status="canonical",
        tier="normal",
        payload=(
            '{"term":"hard 删","synonyms":[],'
            '"primary_database":"test_db","primary_collection":"test_coll","db_type":"mongodb"}'
        ),
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)
    entry_id = entry.id

    from app.knowledge.knowledge_retriever import parse_entry_payload
    await asyncio.to_thread(
        upsert_knowledge_entry,
        slug=ns.slug,
        entry_id=entry.id,
        content=entry.content,
        tier=entry.tier,
        namespace_id=entry.namespace_id,
        entry_type=entry.entry_type,
        status=entry.status,
        payload=parse_entry_payload(entry.payload),
    )
    assert _chromadb_count(ns.slug) == 1

    r = await http_client.delete(
        f"/api/knowledge/{entry_id}?mode=hard&reason=合规要求"
    )
    assert r.status_code == 204, r.text

    remaining = await db_session.get(KnowledgeEntry, entry_id)
    assert remaining is None
    assert _chromadb_count(ns.slug) == 0

    logs = (await db_session.scalars(
        select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == entry_id)
    )).all()
    assert len(logs) == 1
    assert logs[0].action == "hard_delete"
    assert logs[0].from_status == "canonical"
    assert logs[0].to_status == "rejected"
    assert logs[0].reason == "合规要求"
    assert logs[0].actor_id == admin_user.id
