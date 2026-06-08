"""P0-3 Task 4: estimate_query_cost emit cost_warning 测试 (find 路径超阈)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_estimate_query_cost_emits_cost_warning_when_overflow(monkeypatch):
    """find 路径 estimated_docs 超阈 → emit cost_warning, 字段完整."""
    from app.config import settings
    from app.engine.tools import cost_tools

    async def fake_estimate_find(db_, collection, filter):
        return {
            "estimated_docs": settings.query_cost_single_layer_limit + 1000,
            "hit_indexes": [],
            "warning": "single_layer_overflow",
        }

    monkeypatch.setattr(cost_tools, "_estimate_find", fake_estimate_find)
    monkeypatch.setattr(
        cost_tools, "get_mongo_db", AsyncMock(return_value=MagicMock())
    )
    monkeypatch.setattr(cost_tools, "close_db", lambda x: None)

    fake_emit = AsyncMock()
    result = await cost_tools.estimate_query_cost(
        namespace_id=1, collection="c", filter={}, database="db_t",
        sse_emit=fake_emit,
    )

    assert result["warning"] is not None
    fake_emit.assert_awaited()
    emitted = [c.args[0] for c in fake_emit.await_args_list]
    warns = [e for e in emitted if e.get("event") == "cost_warning"]
    assert len(warns) == 1
    data = warns[0]["data"]
    assert data["estimated_docs"] > settings.query_cost_single_layer_limit
    assert data["threshold"] == settings.query_cost_single_layer_limit
    assert "advice" in data


@pytest.mark.asyncio
async def test_estimate_query_cost_no_warning_under_threshold(monkeypatch):
    """find 路径 estimated_docs 未超阈 → 不 emit cost_warning."""
    from app.engine.tools import cost_tools

    async def fake_estimate_find(db_, collection, filter):
        return {"estimated_docs": 100, "hit_indexes": [], "warning": None}

    monkeypatch.setattr(cost_tools, "_estimate_find", fake_estimate_find)
    monkeypatch.setattr(
        cost_tools, "get_mongo_db", AsyncMock(return_value=MagicMock())
    )
    monkeypatch.setattr(cost_tools, "close_db", lambda x: None)

    fake_emit = AsyncMock()
    await cost_tools.estimate_query_cost(
        namespace_id=1, collection="c", filter={}, database="db_t",
        sse_emit=fake_emit,
    )

    emitted = [c.args[0] for c in fake_emit.await_args_list]
    warns = [e for e in emitted if e.get("event") == "cost_warning"]
    assert len(warns) == 0


@pytest.mark.asyncio
async def test_estimate_query_cost_aggregate_path_no_cost_warning(monkeypatch):
    """aggregate 路径 (pipeline_stages 非空) 不 emit cost_warning — 设计决策."""
    from app.engine.tools import cost_tools

    async def fake_estimate_aggregate(db_, collection, filter, pipeline_stages):
        return {"mongo_version": "6.0", "explain_raw": {}, "hint": ""}

    monkeypatch.setattr(cost_tools, "_estimate_aggregate", fake_estimate_aggregate)
    monkeypatch.setattr(
        cost_tools, "get_mongo_db", AsyncMock(return_value=MagicMock())
    )
    monkeypatch.setattr(cost_tools, "close_db", lambda x: None)

    fake_emit = AsyncMock()
    await cost_tools.estimate_query_cost(
        namespace_id=1, collection="c", filter={}, database="db_t",
        sse_emit=fake_emit,
        pipeline_stages=[{"$group": {"_id": "$x"}}],
    )

    emitted = [c.args[0] for c in fake_emit.await_args_list]
    warns = [e for e in emitted if e.get("event") == "cost_warning"]
    assert len(warns) == 0
