"""Phase 2 Task 2.2 — terminology refresh pipeline stage.

Sits at 96-99% of run_training_pipeline_with_progress, after parse_status='parsed'.
Atomic contract:
  - Registers _active_workers[terminology_worker_key(repo_id)] for cancel/observability.
  - Status state machine: pending → running → {completed | skipped | failed | cancelled}.
  - TimeoutError / generic Exception are caught (pipeline continues to 100%).
  - CancelledError re-raises (Task 2.3 cancel hook will catch and clean up).
  - finally: always clears _active_workers entry.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import update

from app.config import settings
from app.db.metadata import async_session
from app.engine.repo_worker import _active_workers
from app.knowledge.terminology_refresher import refresh_terms_for_repo
from app.models.git_repo import GitRepo
from app.models.knowledge_audit_log import KnowledgeAuditLog

log = logging.getLogger(__name__)

# ── Status state machine values for GitRepo.term_refresh_status ─────────────
TERM_STATUS_RUNNING = "running"
TERM_STATUS_COMPLETED = "completed"
TERM_STATUS_FAILED = "failed"
TERM_STATUS_SKIPPED = "skipped"
TERM_STATUS_CANCELLED = "cancelled"

# ── Progress percent boundaries (terminology stage) ─────────────────────────
TERM_STAGE_START_PCT = 96
TERM_STAGE_END_PCT = 99

# ── Friendly Chinese messages for RefreshReport.reason ──────────────────────
# Phase 2 Task 2.4: 空 canonical 等已知 skip 场景 → 中文友好消息;
# 未知 reason 走兜底 fallback (保留原始 reason 便于排错).
_SKIPPED_REASON_MESSAGES: dict[str, str] = {
    "no_canonicals": "无业务术语数据(无 canonical), 跳过抽取",
}


def _friendly_skipped_message(reason: str) -> str:
    """Map RefreshReport.reason to user-facing 中文 message.

    已知 reason 走 _SKIPPED_REASON_MESSAGES, 未知 reason fallback 到原始字串
    (避免吞错; 后续新 reason 加进 dict 即可, 不走 elif 链).
    """
    return _SKIPPED_REASON_MESSAGES.get(reason, f"业务术语跳过: {reason}")


def terminology_worker_key(repo_id: int) -> str:
    """Worker registry key for the terminology stage of a repo's pipeline.

    Task 2.3 cancel endpoint must use this same helper to find the task.
    """
    return f"term_{repo_id}"


async def run_terminology_stage(
    repo_id: int,
    ns_id: int,
    repo_label: str,
    on_progress,
    update_repo_fields,
) -> None:
    """Execute the terminology refresh stage. See module docstring for contract.

    Args:
      repo_id, ns_id: scope.
      repo_label: human-friendly repo name for log messages (e.g. _repo_name(url)).
      on_progress: ProgressCallback from trainer.
      update_repo_fields: bound _update_repo_fields(repo_id, **fields) coroutine.
    """
    await on_progress(TERM_STAGE_START_PCT, "开始抽取业务术语...")
    worker_key = terminology_worker_key(repo_id)
    _active_workers[worker_key] = asyncio.current_task()  # type: ignore[assignment]
    await update_repo_fields(repo_id, term_refresh_status=TERM_STATUS_RUNNING)
    try:
        async with async_session() as db:
            rr = await asyncio.wait_for(
                refresh_terms_for_repo(db, ns_id, repo_id),
                timeout=settings.terminology_refresh_timeout_secs,
            )
        if rr.skipped:
            await update_repo_fields(repo_id, term_refresh_status=TERM_STATUS_SKIPPED)
            await on_progress(TERM_STAGE_END_PCT, _friendly_skipped_message(rr.reason))
        else:
            await update_repo_fields(repo_id, term_refresh_status=TERM_STATUS_COMPLETED)
            await on_progress(
                TERM_STAGE_END_PCT,
                f"业务术语: 新增 {len(rr.merged)} 失败 {len(rr.failed)} "
                f"(canonicals={rr.canonicals_seen})",
            )
    except asyncio.TimeoutError:
        await _mark_failed(repo_id, update_repo_fields, on_progress, "业务术语刷新超时, 跳过")
        log.warning("[%s] terminology stage timed out repo_id=%d", repo_label, repo_id)
    except asyncio.CancelledError:
        # Task 2.3 cancel hook: 写 cancelled status + audit_log; finally 仍清注册表.
        await _cleanup_terminology_on_cancel(repo_id, ns_id, repo_label)
        raise
    except Exception as e:  # pragma: no cover - generic fallback
        await _mark_failed(repo_id, update_repo_fields, on_progress, f"业务术语刷新失败: {e}")
        log.exception("[%s] terminology stage failed repo_id=%d: %s", repo_label, repo_id, e)
    finally:
        _active_workers.pop(worker_key, None)


async def _mark_failed(repo_id, update_repo_fields, on_progress, message) -> None:
    """Common failure branch: status=failed + 99% progress message."""
    await update_repo_fields(repo_id, term_refresh_status=TERM_STATUS_FAILED)
    await on_progress(TERM_STAGE_END_PCT, message)


async def _cleanup_terminology_on_cancel(
    repo_id: int, ns_id: int, repo_label: str,
) -> None:
    """CancelledError 兜底: 写 cancelled status + audit_log.

    使用独立 async_session, 不依赖外部 update_repo_fields (后者可能也被 cancel
    污染). 任何异常仅 log, 不重新抛出 — 我们已在 cancel race 中, 不能阻塞
    raise CancelledError.
    """
    try:
        async with async_session() as db:
            await db.execute(
                update(GitRepo)
                .where(GitRepo.id == repo_id)
                .values(term_refresh_status=TERM_STATUS_CANCELLED)
            )
            db.add(KnowledgeAuditLog(
                entry_id=None,
                actor_id=None,
                action="cancel",
                from_status=TERM_STATUS_RUNNING,
                to_status=TERM_STATUS_CANCELLED,
                reason=(
                    f"terminology worker cancelled, repo={repo_id} ns={ns_id}"
                ),
            ))
            await db.commit()
    except Exception as e:  # pragma: no cover - cleanup 失败仅观测
        log.warning(
            "[%s] terminology cancel cleanup failed repo_id=%d: %s",
            repo_label, repo_id, e,
        )
