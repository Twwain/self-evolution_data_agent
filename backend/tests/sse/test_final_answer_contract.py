"""Stage 3 — final_answer SSE 响应契约守卫 (api-contract L2).
stub run_agent_loop 返回含 present_result 的 tool_trace, 断言 final_answer 八键齐全."""
import json

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch

from app.auth import get_current_user, require_admin
from app.db.metadata import get_db
from app.engine.agent_loop import AgentResult
from app.main import app


@pytest.fixture(autouse=True)
def override_deps(db_session, admin_user):
    async def _fake_db():
        yield db_session
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[require_admin] = lambda: admin_user
    app.dependency_overrides[get_db] = _fake_db
    yield
    app.dependency_overrides.clear()


def _parse_final_answer(sse_text: str) -> dict:
    """从 SSE 流文本提取 final_answer 事件的 data JSON."""
    blocks = sse_text.split("\n\n")
    for b in blocks:
        if "event: final_answer" in b:
            for line in b.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
    raise AssertionError("no final_answer event in stream")


@pytest.mark.asyncio
async def test_final_answer_contains_render_contract_keys(test_namespace):
    trace = [
        {"id": "e1", "name": "execute_query", "status": "ok", "input": {},
         "output": {"rows": [
             {"day": "2024-01-01", "region": "north", "amount": 10},
             {"day": "2024-01-02", "region": "north", "amount": 11},
         ], "row_count": 2}},
        {"id": "p1", "name": "present_result", "status": "ok", "input": {},
         "output": {"status": "ok", "ref": "e1",
                    "chart_spec": {"chart_type": "line", "x": "day", "value": "amount"}}},
    ]
    fake_result = AgentResult(
        final_answer="done", iterations=1, stop_reason="end_turn",
        tool_trace=trace, usage_total={},
    )

    async def fake_run(**kwargs):
        await kwargs["sse_emit"]({"event": "agent_started", "data": {"trace_id": "t"}})
        return fake_result

    with patch("app.api.query.run_agent_loop", fake_run), \
         patch("app.api.query._write_query_history", AsyncMock(return_value=7)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            chunks = []
            async with ac.stream("POST", "/api/query/stream",
                                 json={"namespace_id": test_namespace.id,
                                       "question": "q"}) as resp:
                assert resp.status_code == 200
                async for c in resp.aiter_text():
                    chunks.append(c)

    data = _parse_final_answer("".join(chunks))
    for key in ("rows", "columns", "chart_type", "chart_option", "category_column",
                "truncated", "rendered_row_count", "total_row_count"):
        assert key in data, f"final_answer 缺 key: {key}"
    assert data["chart_type"] == "line"
    assert len(data["rows"]) == 2          # 全量, 非 LLM 手抄
    assert data["chart_option"]["series"]  # 有渲染产物
    assert data["truncated"] is False      # 本例未截断
