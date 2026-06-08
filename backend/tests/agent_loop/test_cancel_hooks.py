"""Stage 4 Task 11 — agent_loop CancelledError cleanup hooks (G3 carry-forward) tests.

验证两条不变量:
1. CancelledError 触发后, 同 trace_id 的 PendingClarification(status=pending) 被翻成 'abandoned'.
2. 写一条 KnowledgeAuditLog action='cancel' actor_id=NULL entry_id=NULL,
   reason 含 trace_id (运维可定位会话).

cancel 路径必须健壮: cleanup 失败仅 log.warning, 不能阻挡 raise CancelledError.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.engine import agent_loop as agent_loop_mod
from app.engine.agent_loop import run_agent_loop
from app.engine.llm import ToolUseResponse
from app.models import Namespace
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.pending_clarification import PendingClarification

# ════════════════════════════════════════════
#  helpers
# ════════════════════════════════════════════


def _patch_session_factory(monkeypatch, db_session):
    """让 _cleanup_on_cancel 内部 `async with async_session()` 复用测试 db_session.

    `async_session()` 是 async_sessionmaker 实例 — 调用它返回 AsyncSession (async ctx mgr).
    我们替换为返回一个会 yield 出测试 session 的 ctx mgr, 但不真 close (测试侧负责).
    """
    class _SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def _factory():
        return _SessionCtx()

    monkeypatch.setattr("app.db.metadata.async_session", _factory)


async def _slow_llm_factory(started: asyncio.Event):
    """LLM 永久挂住 — 让外层 task.cancel() 能可靠中断."""
    async def _slow_llm(messages, tools, stream_callback=None, **_kw):
        started.set()
        await asyncio.sleep(60)  # 永远不返回
        return ToolUseResponse(text="never", tool_calls=[],
                                stop_reason="end_turn", usage={})
    return _slow_llm


async def _noop_emit(_evt):
    return None


# ════════════════════════════════════════════
#  1. PendingClarification(pending) 翻 abandoned
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cancel_marks_pending_clarification_abandoned(
    db_session, monkeypatch,
):
    _patch_session_factory(monkeypatch, db_session)

    ns = Namespace(slug="t11_ns", name="t11")
    db_session.add(ns)
    await db_session.commit()

    trace_id = "trace-cancel-1"
    pc = PendingClarification(
        session_id=trace_id,
        namespace_id=ns.id,
        original_question="q",
        clarification_questions_json="[]",
        targets_json="[]",
        conditions_json="[]",
        resolved_json="{}",
        pending_cond_ids_json="[]",
        status="pending",
        expires_at=datetime.now() + timedelta(seconds=600),
    )
    db_session.add(pc)
    await db_session.commit()
    pc_id = pc.id

    started = asyncio.Event()
    slow_llm = await _slow_llm_factory(started)

    async def runner():
        await run_agent_loop(
            trace_id=trace_id,
            question="q",
            tools_registry={},
            tool_specs=[],
            sse_emit=_noop_emit,
            user_correction_queue=asyncio.Queue(),
            llm=slow_llm,
            system_prompt="",
        )

    task = asyncio.create_task(runner())
    await asyncio.wait_for(started.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # ── 校验 PendingClarification 状态 ──
    refreshed = await db_session.get(PendingClarification, pc_id)
    await db_session.refresh(refreshed)
    assert refreshed.status == "abandoned", (
        f"expected 'abandoned' got {refreshed.status!r}"
    )

    # ── 注册表清理 (finally 块兑现) ──
    assert trace_id not in agent_loop_mod._active_agent_workers


# ════════════════════════════════════════════
#  2. KnowledgeAuditLog action='cancel' 写入
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cancel_writes_audit_log_with_action_cancel(
    db_session, monkeypatch,
):
    _patch_session_factory(monkeypatch, db_session)

    ns = Namespace(slug="t11b_ns", name="t11b")
    db_session.add(ns)
    await db_session.commit()

    trace_id = "trace-audit-1"
    started = asyncio.Event()
    slow_llm = await _slow_llm_factory(started)

    async def runner():
        await run_agent_loop(
            trace_id=trace_id,
            question="q",
            tools_registry={},
            tool_specs=[],
            sse_emit=_noop_emit,
            user_correction_queue=asyncio.Queue(),
            llm=slow_llm,
            system_prompt="",
        )

    task = asyncio.create_task(runner())
    await asyncio.wait_for(started.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # ── 校验 audit log ──
    rows = (await db_session.execute(
        select(KnowledgeAuditLog).where(
            KnowledgeAuditLog.action == "cancel",
            KnowledgeAuditLog.reason.contains(trace_id),
        )
    )).scalars().all()
    assert len(rows) >= 1, "至少 1 条 cancel audit_log 应被写入"
    row = rows[0]
    assert row.entry_id is None, "系统级 cancel 操作 entry_id 应为 NULL"
    assert row.actor_id is None, "agent_loop cancel 是系统操作 actor_id 应为 NULL"


# ════════════════════════════════════════════
#  3. cleanup 失败不阻 CancelledError 传播
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_block_cancel_propagation(
    db_session, monkeypatch, caplog,
):
    """cleanup 内部抛错 → 仅 log.warning, CancelledError 仍向外传播."""
    # 故意让 async_session 工厂抛错, 模拟 DB 故障
    class _BoomCtx:
        async def __aenter__(self):
            raise RuntimeError("simulated DB outage")

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr("app.db.metadata.async_session", lambda: _BoomCtx())

    started = asyncio.Event()
    slow_llm = await _slow_llm_factory(started)

    async def runner():
        await run_agent_loop(
            trace_id="trace-boom-1",
            question="q",
            tools_registry={},
            tool_specs=[],
            sse_emit=_noop_emit,
            user_correction_queue=asyncio.Queue(),
            llm=slow_llm,
            system_prompt="",
        )

    task = asyncio.create_task(runner())
    await asyncio.wait_for(started.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task  # ← 关键: 即使 cleanup 失败, CancelledError 仍传播
