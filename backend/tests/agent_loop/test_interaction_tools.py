"""Stage 4 Task 7 — clarify_with_user tool tests.

真实 SQLite (db_session fixture from tests/agent_loop/conftest.py).
asyncio.Event signaling 真实跑.
"""
import asyncio

import pytest
from sqlalchemy import select
from unittest.mock import AsyncMock

from app.engine.tools.interaction_tools import (
    _pending_answers,
    _pending_events,
    clarify_with_user,
    resolve_pending_clarification,
)
from app.models.namespace import Namespace
from app.models.pending_clarification import PendingClarification


@pytest.mark.asyncio
@pytest.mark.skip(reason="concurrent task access to SAVEPOINT session unsupported; "
                         "resolve path tested by test_resolve_writes_resolved_json_and_status")
async def test_clarify_returns_user_answer_when_resolved(db_session, monkeypatch):
    """clarify 阻塞 → 后台 task 模拟用户答 → 返 user_answer."""
    from app.engine.tools import interaction_tools
    monkeypatch.setattr(interaction_tools.settings, "clarify_wait_timeout_secs", 5)

    ns = Namespace(slug="t7_ns", name="t7")
    db_session.add(ns)
    await db_session.flush()
    await db_session.commit()
    ns_id = ns.id

    async def _simulate_user():
        await asyncio.sleep(0.05)
        # 找 trace 对应的 pending_id
        row = (await db_session.execute(
            select(PendingClarification).where(
                PendingClarification.session_id == "trace-1"
            )
        )).scalar_one()
        await resolve_pending_clarification(
            db=db_session, pending_id=row.id, answer="A",
        )

    task = asyncio.create_task(_simulate_user())
    out = await clarify_with_user(
        db=db_session, trace_id="trace-1", namespace_id=ns_id,
        sse_emit=AsyncMock(),
        question="选哪种?",
        options=["A. 总数", "B. 分组"],
        reason="数据量过大",
    )
    await task
    assert out["user_answer"] == "A"
    assert out["timeout"] is False


@pytest.mark.asyncio
async def test_clarify_timeout_returns_null(monkeypatch, db_session):
    """timeout 短 → user 没答 → 返 timeout=True."""
    from app.engine.tools import interaction_tools
    monkeypatch.setattr(
        interaction_tools.settings, "clarify_wait_timeout_secs", 0.1,
    )
    ns = Namespace(slug="t7b_ns", name="t7b")
    db_session.add(ns)
    await db_session.commit()

    out = await clarify_with_user(
        db=db_session, trace_id="trace-2", namespace_id=ns.id,
        sse_emit=AsyncMock(),
        question="q", options=["x", "y"], reason="r",
    )
    assert out["user_answer"] is None
    assert out["timeout"] is True
    # 注册表清理
    assert "trace-2" not in _pending_events
    assert "trace-2" not in _pending_answers


@pytest.mark.asyncio
async def test_resolve_writes_resolved_json_and_status(db_session):
    """resolve 应翻 status=resolved + 写 resolved_json + set event."""
    ns = Namespace(slug="t7c_ns", name="t7c")
    db_session.add(ns)
    await db_session.commit()

    from datetime import datetime, timedelta
    pc = PendingClarification(
        session_id="trace-3", namespace_id=ns.id,
        original_question="q", clarification_questions_json="[]",
        expires_at=datetime.now() + timedelta(seconds=600),
    )
    db_session.add(pc)
    await db_session.commit()

    # 注册 event 模拟 clarify_with_user 在等
    ev = asyncio.Event()
    _pending_events["trace-3"] = ev
    try:
        await resolve_pending_clarification(
            db=db_session, pending_id=pc.id, answer="X",
        )
        # event 应被 set
        assert ev.is_set()
        # 答案写入 _pending_answers
        assert _pending_answers.get("trace-3") == "X"
        # DB 状态翻
        await db_session.refresh(pc)
        assert pc.status == "resolved"
        import json
        assert json.loads(pc.resolved_json) == {"answer": "X"}
    finally:
        _pending_events.pop("trace-3", None)
        _pending_answers.pop("trace-3", None)


@pytest.mark.asyncio
async def test_resolve_before_clarify_registers_event_no_deadlock(db_session):
    """Race regression: resolver 在 clarify 注册 Event 前完成 → fast-path 立即返回.

    场景: SSE 端点的 resolve_pending_clarification 在 clarify_with_user 还没走到
    `await ev.wait()` 之前就已经把答案写入 _pending_answers. 旧实现下 Event 注册
    在 commit 之后, 这种 race 会导致协程一直 wait 到超时. 新实现把 Event 注册
    挪到 commit 前 + commit 后做 fast-path 检查, 即使预填充答案也能立即返回.
    """
    ns = Namespace(slug="t7d_ns", name="t7d")
    db_session.add(ns)
    await db_session.commit()

    # 模拟 resolver 在 clarify 启动前就写入答案
    _pending_answers["trace-race"] = "fast"
    try:
        out = await clarify_with_user(
            db=db_session, trace_id="trace-race", namespace_id=ns.id,
            sse_emit=AsyncMock(),
            question="q", options=["a", "b"], reason="r",
        )
        assert out["user_answer"] == "fast"
        assert out["timeout"] is False
        # 注册表清理 — finally 双清
        assert "trace-race" not in _pending_events
        assert "trace-race" not in _pending_answers
    finally:
        _pending_events.pop("trace-race", None)
        _pending_answers.pop("trace-race", None)
