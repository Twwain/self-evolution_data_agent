# backend/tests/sse/test_sse_endpoint.py
import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.engine.agent_loop import AgentResult
from app.engine.sse_manager import register_sse_session, deregister_sse_session
from app.auth import get_current_user, require_admin
from app.db.metadata import get_db


@pytest.fixture(autouse=True)
def override_deps(db_session, admin_user):
    async def _fake_db():
        yield db_session

    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[require_admin] = lambda: admin_user
    app.dependency_overrides[get_db] = _fake_db
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_query_stream_emits_sse_events(test_namespace):
    fake_result = AgentResult(
        final_answer="hello", iterations=1, stop_reason="end_turn",
        tool_trace=[], usage_total={},
    )

    async def fake_run(**kwargs):
        emit = kwargs["sse_emit"]
        await emit({"event": "agent_started", "data": {"trace_id": "t1"}})
        await emit({"event": "text_delta", "data": {"delta": "hello"}})
        await emit({"event": "agent_finished", "data": {"total_iterations": 1}})
        return fake_result

    with patch("app.api.query.run_agent_loop", fake_run), \
         patch("app.api.query._write_query_history", AsyncMock(return_value=42)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            chunks = []
            async with ac.stream(
                "POST", "/api/query/stream",
                json={"namespace_id": test_namespace.id, "question": "test q"},
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")
                async for chunk in resp.aiter_text():
                    chunks.append(chunk)

    full = "".join(chunks)
    assert "event: agent_started" in full
    assert "event: text_delta" in full
    assert "event: agent_finished" in full
    assert "event: final_answer" in full


@pytest.mark.asyncio
async def test_correct_endpoint_puts_abort_to_queue(test_namespace):
    _, corr_q = register_sse_session("trace-abort")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/query/stream/trace-abort/correct",
                json={"correction_type": "abort", "step_id": ""},
            )
        assert resp.status_code == 200
        item = corr_q.get_nowait()
        assert item["correction_type"] == "abort"
    finally:
        deregister_sse_session("trace-abort")


@pytest.mark.asyncio
async def test_correct_endpoint_404_unknown_trace():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/query/stream/nonexistent-xyz/correct",
            json={"correction_type": "abort", "step_id": ""},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_active_workers_returns_list():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/query/active_workers")
    assert resp.status_code == 200
    body = resp.json()
    assert "trace_ids" in body
    assert isinstance(body["count"], int)
