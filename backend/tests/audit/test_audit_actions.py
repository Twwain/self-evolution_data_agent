"""Stage 3 Task 2 — POST /api/knowledge/audit/{id}/{approve,reject}.

6 用例覆盖:
    1. proposed → approve → canonical + audit_log(approve)
    2. proposed + supersede 旧 canonical → 新 canonical + 旧 superseded + 双 audit
    3. canonical → approve → 422 invalid_state_transition
    4. proposed → reject → rejected + audit_log(reject from=proposed)
    5. canonical → reject → rejected + ChromaDB 删向量 + audit_log(reject from=canonical)
    6. reject body 缺 reason → 422 (Pydantic Field min_length=1)
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


# ─────────────────────────────────────────────────────────────────
# Fixtures (照抄 test_audit_queue.py 同名 pattern, 不互相 import)
# ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="admin_audit_act",
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
    n = Namespace(name="aa_ns", slug="aa_ns", description="")
    db_session.add(n)
    await db_session.commit()
    await db_session.refresh(n)
    return n


# ─────────────────────────────────────────────────────────────────
# 测试用例
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_proposed_to_canonical_writes_audit(
    http_client: AsyncClient,
    db_session: AsyncSession,
    admin_user: User,
    ns: Namespace,
    chroma_isolated,
) -> None:
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="订单",
        source="manual",
        status="proposed",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await http_client.post(
        f"/api/knowledge/audit/{entry.id}/approve",
        json={"reason": "looks good"},
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
    assert logs[0].action == "approve"
    assert logs[0].from_status == "proposed"
    assert logs[0].to_status == "canonical"
    assert logs[0].actor_id == admin_user.id


@pytest.mark.asyncio
async def test_approve_with_supersede_old_canonical_marked_superseded(
    http_client: AsyncClient,
    db_session: AsyncSession,
    admin_user: User,
    ns: Namespace,
    chroma_isolated,
) -> None:
    old = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="订单-旧",
        source="manual",
        status="canonical",
        tier="normal",
    )
    new = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="订单-新",
        source="manual",
        status="proposed",
        tier="normal",
    )
    db_session.add_all([old, new])
    await db_session.commit()
    await db_session.refresh(old)
    await db_session.refresh(new)

    r = await http_client.post(
        f"/api/knowledge/audit/{new.id}/approve",
        json={"supersede_ids": [old.id], "reason": "替换旧版"},
    )
    assert r.status_code == 200, r.text

    await db_session.refresh(new)
    await db_session.refresh(old)
    assert new.status == "canonical"
    assert old.status == "superseded"
    assert old.is_superseded is True
    assert old.superseded_by == new.id

    new_logs = (await db_session.scalars(
        select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == new.id)
    )).all()
    old_logs = (await db_session.scalars(
        select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == old.id)
    )).all()
    assert len(new_logs) == 1
    assert new_logs[0].action == "approve"
    assert len(old_logs) == 1
    assert old_logs[0].action == "supersede"
    assert old_logs[0].from_status == "canonical"
    assert old_logs[0].to_status == "superseded"
    assert old_logs[0].actor_id == admin_user.id


@pytest.mark.asyncio
async def test_approve_invalid_state_returns_422(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        content="已审过的",
        source="manual",
        status="canonical",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await http_client.post(
        f"/api/knowledge/audit/{entry.id}/approve",
        json={"reason": ""},
    )
    assert r.status_code == 422
    assert "invalid_state_transition" in r.json()["detail"]


@pytest.mark.asyncio
async def test_reject_proposed_writes_audit(
    http_client: AsyncClient,
    db_session: AsyncSession,
    admin_user: User,
    ns: Namespace,
    chroma_isolated,
) -> None:
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        content="待拒",
        source="manual",
        status="proposed",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await http_client.post(
        f"/api/knowledge/audit/{entry.id}/reject",
        json={"reason": "bad"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "rejected"

    await db_session.refresh(entry)
    assert entry.status == "rejected"
    assert entry.reviewed_by_id == admin_user.id

    logs = (await db_session.scalars(
        select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == entry.id)
    )).all()
    assert len(logs) == 1
    assert logs[0].action == "reject"
    assert logs[0].from_status == "proposed"
    assert logs[0].to_status == "rejected"
    assert logs[0].reason == "bad"


@pytest.mark.asyncio
async def test_reject_canonical_triggers_chromadb_delete(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        content="待下线",
        source="manual",
        status="canonical",
        tier="normal",
        payload='{"term":"待下线","synonyms":[],"primary_database":"test_db","primary_collection":"test_coll","db_type":"mongodb"}',
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    # ── 真实 ChromaDB upsert: 写一条向量, 等会儿验证 reject 后被删 ──
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

    # 验证 ChromaDB 已写入 (前置)
    from app.engine.embedding import get_embedding_function
    from app.engine.registry import get_chroma_client
    coll = get_chroma_client().get_collection(
        f"ns_{ns.slug}_knowledge",
        embedding_function=get_embedding_function(),  # type: ignore[arg-type]
    )
    assert coll.count() == 1

    r = await http_client.post(
        f"/api/knowledge/audit/{entry.id}/reject",
        json={"reason": "下线"},
    )
    assert r.status_code == 200, r.text

    await db_session.refresh(entry)
    assert entry.status == "rejected"

    # ChromaDB 已被清空
    coll = get_chroma_client().get_collection(
        f"ns_{ns.slug}_knowledge",
        embedding_function=get_embedding_function(),  # type: ignore[arg-type]
    )
    assert coll.count() == 0

    logs = (await db_session.scalars(
        select(KnowledgeAuditLog).where(KnowledgeAuditLog.entry_id == entry.id)
    )).all()
    assert len(logs) == 1
    assert logs[0].from_status == "canonical"
    assert logs[0].to_status == "rejected"


@pytest.mark.asyncio
async def test_reject_missing_reason_422(
    http_client: AsyncClient,
    db_session: AsyncSession,
    ns: Namespace,
    chroma_isolated,
) -> None:
    entry = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        content="x",
        source="manual",
        status="proposed",
        tier="normal",
    )
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await http_client.post(
        f"/api/knowledge/audit/{entry.id}/reject",
        json={},
    )
    assert r.status_code == 422
