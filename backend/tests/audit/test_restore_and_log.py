"""Stage 3 Task 4 — POST /api/knowledge/{id}/restore + GET /audit/{id}/log.

3 用例覆盖:
    1. rejected → restore → canonical + audit_log(restore) + ChromaDB upsert
    2. proposed → restore → 422 invalid_state_transition
    3. /log 端点按 created_at asc 返完整字段
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db.metadata import get_db
from app.main import app
from app.models import KnowledgeEntry, Namespace
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.user import User


# ─────────────────────────────────────────────────────────────────
# Fixtures (照抄 test_audit_actions.py 同名 pattern, 保持隔离)
# ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="admin_audit_restore",
        password_hash="x",
        role="admin",
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
    app.dependency_overrides[require_admin] = lambda: admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def ns(db_session: AsyncSession) -> Namespace:
    n = Namespace(name="ar_ns", slug="ar_ns", description="")
    db_session.add(n)
    await db_session.commit()
    await db_session.refresh(n)
    return n


# ─────────────────────────────────────────────────────────────────
# 测试用例
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_rejected_to_canonical_writes_audit_and_chromadb(
    http_client: AsyncClient,
    db_session: AsyncSession,
    admin_user: User,
    ns: Namespace,
    chroma_isolated,
) -> None:
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="重启用术语",
        source="manual",
        status="rejected",
        tier="normal",
        payload='{"term":"重启用术语","synonyms":[],"primary_database":"test_db","primary_collection":"test_coll","db_type":"mongodb"}',
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await http_client.post(
        f"/api/knowledge/{entry.id}/restore",
        json={"reason": "重新启用"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "canonical"

    await db_session.refresh(entry)
    assert entry.status == "canonical"
    assert entry.reviewed_by_id == admin_user.id
    assert entry.reviewed_at is not None

    logs = (await db_session.scalars(
        select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == entry.id)
    )).all()
    assert len(logs) == 1
    assert logs[0].action == "restore"
    assert logs[0].from_status == "rejected"
    assert logs[0].to_status == "canonical"
    assert logs[0].actor_id == admin_user.id
    assert logs[0].reason == "重新启用"

    # ── ChromaDB 已 upsert (canonical + tier=normal 才进 RAG) ──
    from app.engine.embedding import get_embedding_function
    from app.engine.registry import get_chroma_client
    coll = get_chroma_client().get_collection(
        f"ns_{ns.slug}_knowledge",
        embedding_function=get_embedding_function(),  # type: ignore[arg-type]
    )
    assert coll.count() == 1


@pytest.mark.asyncio
async def test_restore_non_rejected_returns_422(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        content="尚未拒绝的",
        source="manual",
        status="proposed",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await http_client.post(
        f"/api/knowledge/{entry.id}/restore",
        json={"reason": "试试"},
    )
    assert r.status_code == 422
    assert "invalid_state_transition" in r.json()["detail"]


@pytest.mark.asyncio
async def test_log_returns_chronological_audit_entries(
    http_client: AsyncClient,
    db_session: AsyncSession,
    admin_user: User,
    ns: Namespace,
    chroma_isolated,
) -> None:
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        content="多次审计",
        source="manual",
        status="proposed",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    # ── 手工塞 3 条 audit, 显式控制 created_at 验排序 ──
    base = datetime(2026, 4, 1, 10, 0, 0)
    db_session.add_all([
        KnowledgeAuditLog(
            entry_id=entry.id, actor_id=admin_user.id, action="approve",
            from_status="proposed", to_status="canonical",
            reason="approve-1", diff_json="{}",
            created_at=base,
        ),
        KnowledgeAuditLog(
            entry_id=entry.id, actor_id=admin_user.id, action="edit",
            from_status="canonical", to_status="canonical",
            reason="edit-2", diff_json='{"content":{"before":"x","after":"y"}}',
            created_at=base + timedelta(minutes=10),
        ),
        KnowledgeAuditLog(
            entry_id=entry.id, actor_id=admin_user.id, action="reject",
            from_status="canonical", to_status="rejected",
            reason="reject-3", diff_json="{}",
            created_at=base + timedelta(minutes=20),
        ),
    ])
    await db_session.commit()

    r = await http_client.get(f"/api/knowledge/audit/{entry.id}/log")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [r["action"] for r in rows] == ["approve", "edit", "reject"]
    assert [r["reason"] for r in rows] == ["approve-1", "edit-2", "reject-3"]
    # 字段完整性 — 每条都含审计契约 8 字段 + id
    for row in rows:
        assert set(row.keys()) >= {
            "id", "entry_id", "actor_id", "action",
            "from_status", "to_status", "reason", "diff_json", "created_at",
        }
    assert rows[1]["diff_json"] == '{"content":{"before":"x","after":"y"}}'
