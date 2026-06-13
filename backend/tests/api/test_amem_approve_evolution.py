"""Stage 2 抓手 D — approve 路径合并/补充/覆盖语义.

使用 function-scoped engine 避免 session-scoped event_loop 冲突.
"""
import json
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.base import Base
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry
from app.models.user import User

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(eng.sync_engine, "connect")
    def _set_tz(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("SET timezone = 'Asia/Shanghai'")
        cur.close()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    """Per-test session with SAVEPOINT rollback."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        await conn.begin_nested()
        sess = AsyncSession(bind=conn, expire_on_commit=False)

        @event.listens_for(sess.sync_session, "after_transaction_end")
        def _restart(sync_session, transaction):
            if transaction.nested and not transaction._parent.nested:
                sync_session.begin_nested()

        yield sess
        await sess.close()
        await trans.rollback()


@pytest_asyncio.fixture
async def client(session):
    """ASGI client with fake admin + session override."""
    from app.auth import get_current_user
    from app.db.metadata import get_db
    from app.main import app

    async def _fake_admin():
        return User(id=1, username="admin", role="super_admin", password_hash="x")

    async def _fake_db():
        yield session

    app.dependency_overrides[get_current_user] = _fake_admin
    app.dependency_overrides[get_current_user] = _fake_admin
    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_equivalent_approve_supersedes_old(session, client):
    """equivalent: approve 新条目 → 老条目 superseded."""
    old = KnowledgeEntry(
        namespace_id=None, entry_type="rule",
        content="活跃用户=过去30天登录用户",
        source="manual", status="canonical", tier="normal",
    )
    new = KnowledgeEntry(
        namespace_id=None, entry_type="rule",
        content="活跃用户指30天内有过登录的用户",
        source="agent_learn", status="proposed", tier="normal",
    )
    session.add_all([old, new])
    await session.commit()
    await session.refresh(old)
    await session.refresh(new)

    new.related_entry_ids_json = json.dumps([{
        "related_entry_id": old.id, "relation": "equivalent",
        "llm_reason": "语义等价", "detected_at": "2026-05-23T10:00:00",
    }])
    await session.commit()

    resp = await client.post(
        f"/api/knowledge/audit/{new.id}/approve",
        json={},
    )
    assert resp.status_code == 200

    await session.refresh(old)
    await session.refresh(new)
    assert old.status == "superseded"
    assert old.superseded_by == new.id
    assert new.status == "canonical"

    audit = (await session.execute(
        select(KnowledgeAuditLog).where(
            KnowledgeAuditLog.entry_id == old.id,
            KnowledgeAuditLog.action == "supersede",
        )
    )).scalar_one()
    assert "A-MEM equivalent" in audit.reason


@pytest.mark.asyncio
async def test_conflict_approve_rejects_old(session, client):
    """conflict: approve 新条目 → 老条目 rejected."""
    old = KnowledgeEntry(
        namespace_id=None, entry_type="rule",
        content="VIP=过去30天消费≥1000",
        source="manual", status="canonical", tier="normal",
    )
    new = KnowledgeEntry(
        namespace_id=None, entry_type="rule",
        content="VIP=本月消费≥1000",
        source="agent_learn", status="proposed", tier="normal",
    )
    session.add_all([old, new])
    await session.commit()
    await session.refresh(old)
    await session.refresh(new)

    new.related_entry_ids_json = json.dumps([{
        "related_entry_id": old.id, "relation": "conflict",
        "llm_reason": "统计周期冲突", "detected_at": "2026-05-23T10:00:00",
    }])
    await session.commit()

    resp = await client.post(
        f"/api/knowledge/audit/{new.id}/approve",
        json={},
    )
    assert resp.status_code == 200

    await session.refresh(old)
    await session.refresh(new)
    assert old.status == "rejected"
    assert new.status == "canonical"


@pytest.mark.asyncio
async def test_supplement_approve_links_both(session, client):
    """supplement: approve 新条目 → 双向链接, 老条目保持 canonical."""
    old = KnowledgeEntry(
        namespace_id=None, entry_type="rule",
        content="VIP=月消费≥1000", source="manual",
        status="canonical", tier="normal",
    )
    new = KnowledgeEntry(
        namespace_id=None, entry_type="rule",
        content="VIP 享免邮特权", source="agent_learn",
        status="proposed", tier="normal",
    )
    session.add_all([old, new])
    await session.commit()
    await session.refresh(old)
    await session.refresh(new)

    new.related_entry_ids_json = json.dumps([{
        "related_entry_id": old.id, "relation": "supplement",
        "llm_reason": "补充权益维度", "detected_at": "2026-05-23T10:00:00",
    }])
    await session.commit()

    resp = await client.post(
        f"/api/knowledge/audit/{new.id}/approve",
        json={},
    )
    assert resp.status_code == 200

    await session.refresh(old)
    await session.refresh(new)
    assert old.status == "canonical"
    assert new.status == "canonical"

    old_links = json.loads(old.related_entry_ids_json or "[]")
    assert any(
        r["related_entry_id"] == new.id and r["relation"] == "supplement"
        for r in old_links
    )


@pytest.mark.asyncio
async def test_concurrent_approve_short_circuits(session, client):
    """老条目状态非 canonical 时演化跳过 (已被处理)."""
    old = KnowledgeEntry(
        namespace_id=None, entry_type="rule",
        content="foo", source="manual", status="superseded",
        tier="normal", is_superseded=True,
    )
    new = KnowledgeEntry(
        namespace_id=None, entry_type="rule",
        content="bar", source="agent_learn", status="proposed", tier="normal",
    )
    session.add_all([old, new])
    await session.commit()
    await session.refresh(old)
    await session.refresh(new)

    new.related_entry_ids_json = json.dumps([{
        "related_entry_id": old.id, "relation": "equivalent",
        "llm_reason": "x", "detected_at": "2026-05-23T10:00:00",
    }])
    await session.commit()

    resp = await client.post(
        f"/api/knowledge/audit/{new.id}/approve",
        json={},
    )
    assert resp.status_code == 200

    await session.refresh(old)
    assert old.status == "superseded"  # 不再变化, 短路成功
