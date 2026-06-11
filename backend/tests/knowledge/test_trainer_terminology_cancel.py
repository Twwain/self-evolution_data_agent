"""Phase 2 Task 2.3 — terminology cancel hook 验证.

╔══════════════════════════════════════════════════════════════════════════╗
║  目标:                                                                   ║
║    1. CancelledError 触发 _cleanup_terminology_on_cancel:                ║
║       - GitRepo.term_refresh_status='cancelled'                          ║
║       - KnowledgeAuditLog(action='cancel', reason 含 ns/repo)            ║
║       - 1.5s 内停止                                                      ║
║    2. cancel endpoint 同时取消 main worker + terminology worker          ║
║    3. cancel endpoint 仅 terminology 在跑时 (worker_id 已空) 仍可触发    ║
║                                                                          ║
║  夹具策略 (按 §06 — 不 mock 知识层真实数据):                             ║
║    - SQLite + GitRepo / Namespace 真实写库 (async_session fixture)       ║
║    - refresh_terms_for_repo stub 用 asyncio.sleep 让 task hang           ║
║    - cancel endpoint 测试用 admin_client + monkeypatch worker registry   ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.engine import repo_worker as worker_module
from app.knowledge import trainer_terminology_stage as term_stage_module
from app.knowledge.trainer_terminology_stage import (
    TERM_STATUS_CANCELLED,
    run_terminology_stage,
    terminology_worker_key,
)
from app.models.git_repo import GitRepo
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.namespace import Namespace


# ══════════════════════════════════════════════════════════════════════════════
#  Shared fixture — ns + repo 用于 cancel 直接验证
# ══════════════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def seeded_repo_for_cancel(async_session) -> tuple[int, int, str]:
    """ns + repo, 返回 (ns_id, repo_id, ns_slug)."""
    async with async_session() as db:
        ns = Namespace(name="ns_term_cancel", slug="ns_term_cancel", description="phase2-term-cancel")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        repo = GitRepo(
            namespace_id=ns.id,
            url="https://example.invalid/term-cancel.git",
            branch="master",
            worker_id="fake-worker-uuid",
        )
        db.add(repo)
        await db.commit()
        await db.refresh(repo)
        return ns.id, repo.id, ns.slug


def _patch_async_session(monkeypatch: pytest.MonkeyPatch, factory) -> None:
    """让 trainer_terminology_stage 内部 async_session 走测试 SQLite."""
    monkeypatch.setattr(term_stage_module, "async_session", factory)


# ══════════════════════════════════════════════════════════════════════════════
#  Test 1 — cancel 在 1.5s 内停止 + 写 cancelled status + audit log
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_terminology_cancel_within_1_5s(
    monkeypatch: pytest.MonkeyPatch,
    async_session,
    seeded_repo_for_cancel,
):
    ns_id, repo_id, _ns_slug = seeded_repo_for_cancel

    _patch_async_session(monkeypatch, async_session)

    saw_registered = asyncio.Event()

    async def _slow_refresh(db, ns_id_arg):
        saw_registered.set()
        await asyncio.sleep(5)  # hang, 直到外部 cancel
        return None  # pragma: no cover - 永不到达

    monkeypatch.setattr(term_stage_module, "refresh_namespace_terminology", _slow_refresh)

    async def on_progress(percent: int, message: str) -> None:
        return None

    async def update_repo_fields(rid: int, **fields) -> None:
        async with async_session() as db:
            row = await db.get(GitRepo, rid)
            for k, v in fields.items():
                setattr(row, k, v)
            await db.commit()

    task = asyncio.create_task(
        run_terminology_stage(
            repo_id=repo_id,
            ns_id=ns_id,
            repo_label="term-cancel",
            on_progress=on_progress,
            update_repo_fields=update_repo_fields,
        )
    )

    # ── 等 refresh 进入, 注册表已 ready ──
    await asyncio.wait_for(saw_registered.wait(), timeout=2.0)
    worker_key = terminology_worker_key(repo_id)
    assert worker_key in worker_module._active_workers

    # ── 触发取消 ──
    task.cancel()

    # ── 1.5s 内必须抛 CancelledError ──
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.5)

    # ── status=cancelled ──
    async with async_session() as db:
        row = await db.scalar(select(GitRepo).where(GitRepo.id == repo_id))
        assert row.term_refresh_status == TERM_STATUS_CANCELLED, \
            f"cancel 后 term_refresh_status 应为 cancelled, 实测 {row.term_refresh_status}"

        # ── audit_log 写入 cancel 记录 ──
        rows = (await db.execute(
            select(KnowledgeAuditLog).where(KnowledgeAuditLog.action == "cancel")
        )).scalars().all()
        assert len(rows) == 1, f"预期 1 条 cancel 审计, 实测 {len(rows)}"
        log = rows[0]
        assert log.from_status == "running"
        assert log.to_status == TERM_STATUS_CANCELLED
        assert log.entry_id is None
        assert log.actor_id is None
        assert "terminology worker cancelled" in log.reason
        assert f"repo={repo_id}" in log.reason
        assert f"ns={ns_id}" in log.reason

    # ── _active_workers finally 兜底清理 ──
    assert worker_key not in worker_module._active_workers, \
        f"cancel 后注册表应已清理: {list(worker_module._active_workers.keys())}"


# ══════════════════════════════════════════════════════════════════════════════
#  Test 2 — cancel endpoint 同时取消 main worker + terminology worker
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cancel_endpoint_cancels_both_workers(
    monkeypatch: pytest.MonkeyPatch,
    admin_client,
    db,
):
    # ── 种 ns + repo (worker_id 非空) ──
    ns = Namespace(name="ns_cancel_ep", slug="ns_cancel_ep", description="phase2-cancel-ep")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    repo = GitRepo(
        namespace_id=ns.id,
        url="https://example.invalid/cancel-ep.git",
        branch="master",
        worker_id="fake-worker-uuid",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)

    # ── 注册两个 hangable stub task ──
    main_done = asyncio.Event()
    term_done = asyncio.Event()

    async def _main_stub():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            main_done.set()
            raise

    async def _term_stub():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            term_done.set()
            raise

    main_task = asyncio.create_task(_main_stub())
    term_task = asyncio.create_task(_term_stub())

    worker_module._active_workers["fake-worker-uuid"] = main_task
    worker_module._active_workers[terminology_worker_key(repo.id)] = term_task

    try:
        resp = await admin_client.post(
            f"/api/namespaces/{ns.id}/repos/{repo.id}/cancel"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("cancelled") is True
        assert body.get("terminology_cancelled") is True

        # ── 两个 stub task 都被取消 ──
        await asyncio.wait_for(main_done.wait(), timeout=2.0)
        await asyncio.wait_for(term_done.wait(), timeout=2.0)
    finally:
        # cleanup 残留 task (cancel_worker 已 pop, 但兜底)
        for t in (main_task, term_task):
            if not t.done():
                t.cancel()
            try:
                await t
            except (asyncio.CancelledError, BaseException):  # noqa: BLE001
                pass
        worker_module._active_workers.pop("fake-worker-uuid", None)
        worker_module._active_workers.pop(terminology_worker_key(repo.id), None)


# ══════════════════════════════════════════════════════════════════════════════
#  Test 3 — main worker 已结束, 仅 terminology 在跑时, cancel endpoint 仍可触发
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cancel_endpoint_when_only_terminology_running(
    monkeypatch: pytest.MonkeyPatch,
    admin_client,
    db,
):
    # ── 种 ns + repo (worker_id 已空, 模拟 main 完成) ──
    ns = Namespace(name="ns_term_only", slug="ns_term_only", description="phase2-term-only")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)

    repo = GitRepo(
        namespace_id=ns.id,
        url="https://example.invalid/term-only.git",
        branch="master",
        worker_id="",
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)

    # ── 仅注册 terminology worker ──
    term_done = asyncio.Event()

    async def _term_stub():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            term_done.set()
            raise

    term_task = asyncio.create_task(_term_stub())
    worker_module._active_workers[terminology_worker_key(repo.id)] = term_task

    try:
        resp = await admin_client.post(
            f"/api/namespaces/{ns.id}/repos/{repo.id}/cancel"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("cancelled") is False
        assert body.get("terminology_cancelled") is True

        await asyncio.wait_for(term_done.wait(), timeout=2.0)
    finally:
        if not term_task.done():
            term_task.cancel()
        try:
            await term_task
        except (asyncio.CancelledError, BaseException):  # noqa: BLE001
            pass
        worker_module._active_workers.pop(terminology_worker_key(repo.id), None)
