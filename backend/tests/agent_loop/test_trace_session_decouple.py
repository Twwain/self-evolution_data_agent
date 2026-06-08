"""trace_id / session_id 解耦回归 (修复 agent_traces_trace_id_key 唯一约束冲突).

场景: 用户同一会话内取消重发 → 同 session_id 但应是不同 trace_id, 两条 agent_traces
都能落库, 不撞唯一约束。

cd backend && python -m pytest tests/agent_loop/test_trace_session_decouple.py --timeout=120 --timeout-method=thread
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.engine.agent_loop import _persist_trace
from app.models import AgentTrace


@pytest.mark.asyncio
async def test_same_session_distinct_traces_no_conflict(db_session):
    """同 session_id + 两个不同 trace_id → 两条 agent_traces 共存, 无唯一约束冲突。"""
    session_id = str(uuid.uuid4())
    trace_a = str(uuid.uuid4())
    trace_b = str(uuid.uuid4())

    for tid in (trace_a, trace_b):
        await _persist_trace(
            db=db_session,
            trace_id=tid,
            session_id=session_id,
            namespace_id=None,
            user_query="统计 A 级/B 级品牌资源类型占比",
            tool_trace=[],
            reflection_log=[],
            status="completed",
        )

    rows = (
        await db_session.execute(
            select(AgentTrace).where(AgentTrace.session_id == session_id)
        )
    ).scalars().all()
    trace_ids = {r.trace_id for r in rows}
    assert trace_ids == {trace_a, trace_b}  # 两条都落库
    assert all(r.session_id == session_id for r in rows)  # session 关联保留


@pytest.mark.asyncio
async def test_duplicate_trace_id_still_rejected(db_session):
    """同 trace_id 重复落库仍应冲突 (唯一约束本身不变, 只是不再被 session 复用触发)。"""
    from sqlalchemy.exc import IntegrityError

    tid = str(uuid.uuid4())
    await _persist_trace(
        db=db_session, trace_id=tid, session_id="s1", namespace_id=None,
        user_query="q", tool_trace=[], reflection_log=[], status="completed",
    )
    with pytest.raises(IntegrityError):
        await _persist_trace(
            db=db_session, trace_id=tid, session_id="s1", namespace_id=None,
            user_query="q", tool_trace=[], reflection_log=[], status="completed",
        )


@pytest.mark.asyncio
async def test_session_id_nullable_backward_compat(db_session):
    """session_id 可空 — 旧路径不传也能落库 (nullable 兼容)。"""
    tid = str(uuid.uuid4())
    await _persist_trace(
        db=db_session, trace_id=tid, namespace_id=None,
        user_query="q", tool_trace=[], reflection_log=[], status="completed",
    )
    row = (
        await db_session.execute(select(AgentTrace).where(AgentTrace.trace_id == tid))
    ).scalar_one()
    assert row.session_id is None
