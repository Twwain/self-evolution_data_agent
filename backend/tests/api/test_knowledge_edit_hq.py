"""Phase 3 — knowledge.py:edit_entry HQ 双路径集成测.

验证:
- canonical route_hint 改 content → 自动重生 HQ
- body.hypothetical_queries 给 → 跳过 LLM 直接落库
"""

import json
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from unittest.mock import patch

from app.models.base import Base
from app.models.knowledge_entry import KnowledgeEntry
from app.models.user import User

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/intelligent_statistics_test",
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
    from app.auth import get_current_user, require_admin
    from app.db.metadata import get_db
    from app.main import app

    async def _fake_admin():
        return User(id=1, username="admin", role="admin", password_hash="x")

    async def _fake_db():
        yield session

    app.dependency_overrides[require_admin] = _fake_admin
    app.dependency_overrides[get_current_user] = _fake_admin
    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_edit_content_triggers_hq_regen(session, client):
    """canonical route_hint 改 content → 自动重生 HQ."""
    ke = KnowledgeEntry(
        namespace_id=None,
        entry_type="route_hint",
        content="旧 content",
        tier="normal",
        status="canonical",
        source="manual",
        payload=json.dumps({
            "question_pattern": "x",
            "collection_path": ["c_a", "c_b"],
            "cost_strategy": "default",
        }),
    )
    session.add(ke)
    await session.commit()
    await session.refresh(ke)

    with patch(
        "app.knowledge.hq_writer.generate_hq_with_validation",
        return_value=["新问题"],
    ) as gen_spy, patch(
        "app.knowledge.hq_writer.rewrite_hq_subvectors",
    ) as chroma_spy:
        resp = await client.put(
            f"/api/knowledge/{ke.id}",
            json={"content": "新 content", "reason": "test"},
        )
    assert resp.status_code == 200
    gen_spy.assert_called_once()
    chroma_spy.assert_called_once()

    await session.refresh(ke)
    parsed = json.loads(ke.hypothetical_queries_json)
    assert parsed[0]["q"] == "新问题"
    assert parsed[0]["model"] != "manual"


@pytest.mark.asyncio
async def test_edit_hq_manual_skips_llm(session, client):
    """body.hypothetical_queries 给 → 跳过 LLM 直接落库."""
    ke = KnowledgeEntry(
        namespace_id=None,
        entry_type="route_hint",
        content="原 content",
        tier="normal",
        status="canonical",
        source="manual",
        payload=json.dumps({
            "question_pattern": "x",
            "collection_path": ["c_a"],
            "cost_strategy": "default",
        }),
    )
    session.add(ke)
    await session.commit()
    await session.refresh(ke)

    with patch(
        "app.knowledge.hq_writer.generate_hq_with_validation",
    ) as gen_spy, patch(
        "app.knowledge.hq_writer.rewrite_hq_subvectors",
    ) as chroma_spy:
        resp = await client.put(
            f"/api/knowledge/{ke.id}",
            json={
                "hypothetical_queries": ["手改问题1", "手改问题2"],
                "reason": "manual",
            },
        )
    assert resp.status_code == 200
    gen_spy.assert_not_called()
    chroma_spy.assert_called_once()

    await session.refresh(ke)
    parsed = json.loads(ke.hypothetical_queries_json)
    assert [p["q"] for p in parsed] == ["手改问题1", "手改问题2"]
    assert parsed[0]["model"] == "manual"


@pytest.mark.asyncio
async def test_edit_content_no_hq_for_non_canonical(session, client):
    """proposed 状态改 content → 不触发 HQ 重生."""
    ke = KnowledgeEntry(
        namespace_id=None,
        entry_type="route_hint",
        content="旧 content",
        tier="normal",
        status="proposed",
        source="manual",
        payload=json.dumps({
            "question_pattern": "x",
            "collection_path": ["c_a"],
            "cost_strategy": "default",
        }),
    )
    session.add(ke)
    await session.commit()
    await session.refresh(ke)

    with patch(
        "app.knowledge.hq_writer.generate_hq_with_validation",
    ) as gen_spy, patch(
        "app.knowledge.hq_writer.rewrite_hq_subvectors",
    ):
        resp = await client.put(
            f"/api/knowledge/{ke.id}",
            json={"content": "新 content", "reason": "test"},
        )
    assert resp.status_code == 200
    gen_spy.assert_not_called()
