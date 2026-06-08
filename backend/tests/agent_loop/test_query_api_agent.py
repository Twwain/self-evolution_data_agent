"""Stage 4 Task 10 — /api/query agent loop integration + /cancel tests.

测试范围:
1. 未知 trace_id 走 /cancel → 404
2. 已注册 task → /cancel → 200, task.cancelled() 为真
3. dispatcher 注入 runtime kwargs (db / namespace_id / ns_slug) 到 tool callable
4. dispatcher 不向不接收的工具传 datasource_id, 防 TypeError
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.metadata import get_db
from app.engine.agent_loop import _active_agent_workers
from app.main import app
from app.models.user import User

# ═════════════════════════════════════════════════════════
#  Fixtures
# ═════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="admin_query_agent",
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
    db_session: AsyncSession, admin_user: User,
) -> AsyncGenerator[AsyncClient, None]:
    async def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ═════════════════════════════════════════════════════════
#  /cancel 端点
# ═════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cancel_endpoint_404_when_trace_unknown(http_client: AsyncClient):
    """unknown trace_id → 404."""
    resp = await http_client.post("/api/query/stream/never-existed/cancel")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_endpoint_kills_running_task(http_client: AsyncClient):
    """注册一个 fake task, /cancel 之, 验证 task 被 cancel."""
    async def long_task():
        await asyncio.sleep(10)

    task = asyncio.create_task(long_task())
    _active_agent_workers["t-fake-cancel"] = task
    try:
        resp = await http_client.post("/api/query/stream/t-fake-cancel/cancel")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] is True
        assert body["trace_id"] == "t-fake-cancel"
        assert task.cancelled() or task.done()
    finally:
        _active_agent_workers.pop("t-fake-cancel", None)
        if not task.done():
            task.cancel()


# ═════════════════════════════════════════════════════════
#  Dispatcher: runtime kwargs 注入
# ═════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_dispatcher_binds_runtime_kwargs():
    """build_bound_registry 把 namespace_id / ns_slug / trace_id 注入工具调用.

    注: _bind_one 对需要 db 的工具创建独立 AsyncSession (并发安全设计),
    故不断言 captured["db"] 是传入的字符串, 而断言它是 AsyncSession 实例.
    """
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.engine.agent_loop_dispatcher import build_bound_registry
    from app.engine.tools import registry as registry_mod

    captured: dict = {}

    async def fake_tool(*, db, namespace_id, ns_slug, query):
        captured.update(db=db, namespace_id=namespace_id, ns_slug=ns_slug, query=query)
        return {"ok": True}

    original = registry_mod.REGISTRY
    registry_mod.REGISTRY = {"fake_tool": fake_tool}
    try:
        from unittest.mock import AsyncMock
        bound = build_bound_registry(
            db="FAKE_DB", namespace_id=42, ns_slug="my_ns",
            trace_id="trace-1", sse_emit=AsyncMock(),
        )
        out = await bound["fake_tool"](query="hello")
        assert out == {"ok": True}
        # db 被替换为独立 AsyncSession (并发安全), 不等于传入的字符串
        assert isinstance(captured["db"], AsyncSession)
        assert captured["namespace_id"] == 42
        assert captured["ns_slug"] == "my_ns"
        assert captured["query"] == "hello"
    finally:
        registry_mod.REGISTRY = original


@pytest.mark.asyncio
async def test_dispatcher_skips_unaccepted_kwargs():
    """tool 不接收 sse_emit 等额外 ctx kwarg → 不注入, 不抛 TypeError.

    注: narrow_tool 需要 db, _bind_one 创建独立 AsyncSession (并发安全设计),
    故不断言 captured["db"] 等于传入的字符串, 而断言是 AsyncSession 实例.
    """
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.engine.agent_loop_dispatcher import build_bound_registry
    from app.engine.tools import registry as registry_mod

    captured: dict = {}

    async def narrow_tool(*, db, query):
        captured.update(db=db, query=query)
        return None

    original = registry_mod.REGISTRY
    registry_mod.REGISTRY = {"narrow_tool": narrow_tool}
    try:
        from unittest.mock import AsyncMock
        bound = build_bound_registry(
            db="DB", namespace_id=1, ns_slug="ns",
            trace_id="t", sse_emit=AsyncMock(),
        )
        await bound["narrow_tool"](query="q")
        assert isinstance(captured["db"], AsyncSession)
        assert captured["query"] == "q"
    finally:
        registry_mod.REGISTRY = original
