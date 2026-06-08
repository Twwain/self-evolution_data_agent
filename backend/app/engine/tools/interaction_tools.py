"""Stage 4 Task 7 — clarify_with_user (agent 卡住时问用户, 阻塞到答或超时).

复用 PendingClarification 模型作为 pending 载体.
trace_id 当 session_id (PendingClarification.session_id), 跨 SSE 端点 (Stage 5) 唤醒.

事务契约例外: 此 tool 必须 db.commit() — 跨 session 唤醒需要持久化.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from langfuse import observe
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.tools._mongo_helpers import record_span_io
from app.models.pending_clarification import PendingClarification

log = logging.getLogger(__name__)

# ════════════════════════════════════════════
#  模块级注册表 — 跨 SSE 端点唤醒
# ════════════════════════════════════════════
# trace_id → asyncio.Event (resolve 唤醒)
_pending_events: dict[str, asyncio.Event] = {}
# trace_id → 答案 (resolve 写入, clarify 取)
_pending_answers: dict[str, str] = {}


# ════════════════════════════════════════════
#  clarify_with_user — agent tool, 阻塞到答或超时
# ════════════════════════════════════════════
@observe(name="tool.clarify_with_user")
async def clarify_with_user(
    *, db: AsyncSession, trace_id: str, namespace_id: int,
    sse_emit,
    question: str, options: list[str], reason: str,
) -> dict:
    """Agent 不确定时调此 tool. 阻塞到用户答或超时.

    Stage 5 SSE 端点会:
      - 收到 clarify_request 事件后弹卡给用户
      - 用户答后 POST /api/query/stream/{trace_id}/clarify_response → resolve_pending_clarification
      - resolve 翻 pending status + set asyncio.Event 唤醒此 tool

    事务契约例外: 此 tool 必须 db.commit() — clarify 在主 session 写 PendingClarification,
    Stage 5 端点在另一 session 读+改, 不 commit 跨 session 看不见.

    并发契约: Event 必须在 db.commit() 前注册. 否则 commit 与注册之间存在窗口期,
    若 Stage 5 SSE 端点此刻 resolve, _pending_answers 写入但 Event 尚未注册, 本协程
    将永远 wait_for 到超时. 注册先行后, commit 期间若已 resolve, fast-path 立即返回.
    """
    timeout = settings.clarify_wait_timeout_secs

    # ──────────────────────────────────────────
    # Event 注册 — 必须在 commit 前 (防 fast-path race)
    # ──────────────────────────────────────────
    ev = asyncio.Event()
    _pending_events[trace_id] = ev

    try:
        try:
            pc = PendingClarification(
                session_id=trace_id, namespace_id=namespace_id,
                original_question=question,
                clarification_questions_json=json.dumps(
                    [{"question": question, "options": options, "reason": reason}],
                    ensure_ascii=False,
                ),
                targets_json="[]", conditions_json="[]",
                resolved_json="{}", pending_cond_ids_json="[]",
                status="pending",
                expires_at=datetime.now() + timedelta(seconds=timeout),
            )
            db.add(pc)
            await db.flush()
            await db.commit()  # 例外: 必 commit, 跨 session 唤醒
            pending_id = pc.id

            # ── P0-3 emit clarify_request — 让前端弹卡 ──
            await sse_emit({"event": "clarify_request", "data": {
                "pending_id": pending_id,
                "question": question,
                "options": options,
                "reason": reason,
            }})

            # fast-path: commit 期间 resolver 已写入答案 → 立即唤醒
            if trace_id in _pending_answers:
                ev.set()

            try:
                await asyncio.wait_for(ev.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                log.info("clarify_with_user trace=%s 超时 %ss", trace_id, timeout)
                record_span_io(
                    input={
                        "trace_id": trace_id,
                        "question_preview": question[:100],
                        "option_count": len(options),
                    },
                    output={"timeout": True, "pending_id": pending_id},
                )
                return {"user_answer": None, "timeout": True, "pending_id": pending_id}

            answer = _pending_answers.get(trace_id)
            record_span_io(
                input={
                    "trace_id": trace_id,
                    "question_preview": question[:100],
                    "option_count": len(options),
                },
                output={
                    "timeout": False,
                    "pending_id": pending_id,
                    "got_answer": answer is not None,
                },
            )
            return {"user_answer": answer, "timeout": False, "pending_id": pending_id}
        except Exception as e:
            # 异常路径也落 span — 防丢观测信号
            record_span_io(
                input={
                    "trace_id": trace_id,
                    "question_preview": question[:100],
                    "option_count": len(options),
                },
                output={"error": f"{type(e).__name__}: {e}"},
            )
            raise
    finally:
        # 注册表 + 答案双清 — 防 timeout 后用户迟到答案泄漏 (I2)
        _pending_events.pop(trace_id, None)
        _pending_answers.pop(trace_id, None)


# ════════════════════════════════════════════
#  resolve_pending_clarification — Stage 5 SSE 端点内部 helper
# ════════════════════════════════════════════
async def resolve_pending_clarification(
    *, db: AsyncSession, pending_id: int, answer: str,
) -> None:
    """SSE clarify_response 端点 (Stage 5) 调此函数.

    此 helper 不是 tool, 是 Stage 5 端点的内部依赖. 不套 @observe.
    """
    pc = await db.get(PendingClarification, pending_id)
    if not pc:
        raise ValueError(f"pending {pending_id} 不存在")
    pc.status = "resolved"
    pc.resolved_json = json.dumps({"answer": answer}, ensure_ascii=False)
    await db.commit()

    _pending_answers[pc.session_id] = answer
    if (ev := _pending_events.get(pc.session_id)) is not None:
        ev.set()
