"""Phase 2 Task 2.2 — trainer terminology stage 接通验证.

╔══════════════════════════════════════════════════════════════════════════╗
║  目标:                                                                   ║
║    1. run_training_pipeline_with_progress 末端 (96-99%) 调               ║
║       refresh_terms_for_repo, 进度消息体现 terminology 阶段             ║
║    2. _active_workers["term_{repo_id}"] 注册 + 清理                      ║
║    3. IS_TERMINOLOGY_REFRESH_TIMEOUT_SECS 触发即标 failed,               ║
║       不向上冒泡 abort 整个 pipeline                                     ║
║                                                                          ║
║  夹具策略 (按 §06 — 不 mock 知识层真实数据):                             ║
║    - SQLite + GitRepo / Namespace 真实写库 (async_session fixture)       ║
║    - 只 monkeypatch 重 IO 阶段 (clone/parse/build/train/evaluate/        ║
║      self_answer) + refresh_terms_for_repo 本身 (LLM 抽词成本)           ║
║    - on_progress 用真 callable 收事件, 不打桩                            ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.knowledge import trainer as trainer_module
from app.knowledge import trainer_terminology_stage as term_stage_module
from app.knowledge.parse_result import CodeParseResult, ParseReport, ParserStats
from app.knowledge.terminology_extractor import RefreshReport
from app.knowledge.trainer_terminology_stage import terminology_worker_key
from app.models.git_repo import GitRepo
from app.models.namespace import Namespace


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers — stub 重 IO 阶段, 仅保留 progress + DB 副作用 + terminology 真链路
# ══════════════════════════════════════════════════════════════════════════════

def _stub_heavy_pipeline_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub clone/parse/build/train/evaluate/self_answer/backfill — 让流水线秒走完."""

    def _fake_clone_or_update(url: str, branch: str, repo_id: int):
        return ("/tmp/fake-repo", "cloned")

    def _fake_parse_repository(local_path: str):
        return CodeParseResult(), ParserStats()

    def _fake_build_docs(code_result):
        return ([], [])

    def _fake_evaluate(report, all_trained):
        report.completeness_score = 100
        report.unclear_items = []  # 跳过 self_answer 分支
        return report

    monkeypatch.setattr(trainer_module, "clone_or_update", _fake_clone_or_update)
    monkeypatch.setattr(trainer_module, "parse_repository", _fake_parse_repository)
    monkeypatch.setattr(trainer_module, "_build_docs", _fake_build_docs)
    monkeypatch.setattr(trainer_module, "evaluate_parse_quality", _fake_evaluate)


@pytest_asyncio.fixture
async def seeded_repo_for_terminology(async_session) -> tuple[int, int, str]:
    """ns + repo, 返回 (ns_id, repo_id, ns_slug). 复用 Phase 2 共享形态."""
    async with async_session() as db:
        ns = Namespace(name="ns_term_stage", slug="ns_term_stage", description="phase2-term")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        repo = GitRepo(
            namespace_id=ns.id,
            url="https://example.invalid/term-stage.git",
            branch="master",
        )
        db.add(repo)
        await db.commit()
        await db.refresh(repo)
        return ns.id, repo.id, ns.slug


def _patch_async_session(monkeypatch: pytest.MonkeyPatch, factory) -> None:
    """让 trainer 内部 `async with async_session() as db` 走测试 SQLite."""
    monkeypatch.setattr(trainer_module, "async_session", factory)
    monkeypatch.setattr(term_stage_module, "async_session", factory)


# ══════════════════════════════════════════════════════════════════════════════
#  Test 1 — refresh 被调一次, 进度消息落 96-99% 区间
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_terminology_stage_calls_refresh_and_updates_progress(
    monkeypatch: pytest.MonkeyPatch,
    async_session,
    seeded_repo_for_terminology,
):
    ns_id, repo_id, ns_slug = seeded_repo_for_terminology

    _patch_async_session(monkeypatch, async_session)
    _stub_heavy_pipeline_stages(monkeypatch)

    # ── refresh 探针: 记录入参 + 返伪 RefreshReport ──
    seen_args: list[tuple[Any, ...]] = []

    async def _probe_refresh(db, ns_id_arg):
        seen_args.append((ns_id_arg,))
        return RefreshReport(
            canonicals_seen=3,
            merged=["alpha", "beta"],
            failed=[],
        )

    monkeypatch.setattr(term_stage_module, "refresh_namespace_terminology", _probe_refresh)

    events: list[tuple[int, str]] = []

    async def on_progress(percent: int, message: str) -> None:
        events.append((percent, message))

    report = await trainer_module.run_training_pipeline_with_progress(
        repo_id=repo_id,
        ns_id=ns_id,
        ns_slug=ns_slug,
        repo_url="https://example.invalid/term-stage.git",
        repo_branch="master",
        on_progress=on_progress,
    )

    assert isinstance(report, ParseReport)
    assert seen_args == [(ns_id,)], "refresh_namespace_terminology 应被调一次且入参对齐"

    term_events = [(p, m) for p, m in events if 96 <= p <= 99]
    assert term_events, f"未发现 96-99% 区间的 terminology 事件: {events}"
    assert any("术语" in m or "terminology" in m.lower() for _, m in term_events), \
        f"terminology 事件消息体应含术语关键词: {term_events}"

    # ── 100% 完成事件仍存在, terminology 不破坏 pipeline 末态 ──
    assert events[-1] == (100, "完成"), f"末态须保持 (100, '完成'), 实测 {events[-1]}"

    # ── term_refresh_status 落 completed ──
    async with async_session() as db:
        row = await db.scalar(select(GitRepo).where(GitRepo.id == repo_id))
        assert row.term_refresh_status == "completed", \
            f"term_refresh_status 应为 completed, 实测 {row.term_refresh_status}"


# ══════════════════════════════════════════════════════════════════════════════
#  Test 2 — _active_workers["term_{repo_id}"] 在 refresh 期间注册, 之后清理
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_terminology_worker_registered_and_unregistered(
    monkeypatch: pytest.MonkeyPatch,
    async_session,
    seeded_repo_for_terminology,
):
    ns_id, repo_id, ns_slug = seeded_repo_for_terminology

    _patch_async_session(monkeypatch, async_session)
    _stub_heavy_pipeline_stages(monkeypatch)

    from app.engine import repo_worker as worker_module

    worker_key = terminology_worker_key(repo_id)
    saw_registered = asyncio.Event()
    can_finish = asyncio.Event()

    async def _slow_refresh(db, ns_id_arg):
        # 进入 refresh 时, key 必须已在注册表
        assert worker_key in worker_module._active_workers, \
            f"refresh 进入时 {worker_key} 应已注册, 实测 {list(worker_module._active_workers.keys())}"
        saw_registered.set()
        await can_finish.wait()
        return RefreshReport(canonicals_seen=1, merged=["x"])

    monkeypatch.setattr(term_stage_module, "refresh_namespace_terminology", _slow_refresh)

    async def on_progress(percent: int, message: str) -> None:
        return None

    pipeline_task = asyncio.create_task(
        trainer_module.run_training_pipeline_with_progress(
            repo_id=repo_id,
            ns_id=ns_id,
            ns_slug=ns_slug,
            repo_url="https://example.invalid/term-stage.git",
            repo_branch="master",
            on_progress=on_progress,
        )
    )

    # ── 等 refresh 进入 ──
    await asyncio.wait_for(saw_registered.wait(), timeout=5.0)
    assert worker_key in worker_module._active_workers

    # ── 放行 refresh, 等 pipeline 自然结束 ──
    can_finish.set()
    await asyncio.wait_for(pipeline_task, timeout=5.0)

    # ── 收尾后必须清理 ──
    assert worker_key not in worker_module._active_workers, \
        f"pipeline 完成后 {worker_key} 应已从注册表移除"


# ══════════════════════════════════════════════════════════════════════════════
#  Test 3 — 超时不 abort 整个 pipeline, term_refresh_status=failed
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_terminology_timeout_marks_failed_status(
    monkeypatch: pytest.MonkeyPatch,
    async_session,
    seeded_repo_for_terminology,
):
    ns_id, repo_id, ns_slug = seeded_repo_for_terminology

    _patch_async_session(monkeypatch, async_session)
    _stub_heavy_pipeline_stages(monkeypatch)

    # ── 超时阈值压到 0.01s ──
    monkeypatch.setattr(
        "app.config.settings.terminology_refresh_timeout_secs", 0.01
    )

    async def _hang_refresh(db, ns_id_arg):
        await asyncio.sleep(2)  # 远超 timeout
        return RefreshReport()  # pragma: no cover - 永不到达

    monkeypatch.setattr(term_stage_module, "refresh_namespace_terminology", _hang_refresh)

    events: list[tuple[int, str]] = []

    async def on_progress(percent: int, message: str) -> None:
        events.append((percent, message))

    # ── pipeline 必须正常结束, 不抛 TimeoutError ──
    report = await trainer_module.run_training_pipeline_with_progress(
        repo_id=repo_id,
        ns_id=ns_id,
        ns_slug=ns_slug,
        repo_url="https://example.invalid/term-stage.git",
        repo_branch="master",
        on_progress=on_progress,
    )
    assert isinstance(report, ParseReport)
    assert events[-1] == (100, "完成"), f"超时分支 pipeline 末态须保持完成: {events[-1]}"

    # ── status=failed + 99% 出现超时消息 ──
    async with async_session() as db:
        row = await db.scalar(select(GitRepo).where(GitRepo.id == repo_id))
        assert row.term_refresh_status == "failed", \
            f"超时 → term_refresh_status=failed, 实测 {row.term_refresh_status}"

    timeout_msgs = [m for p, m in events if p == 99 and "超时" in m]
    assert timeout_msgs, f"99% 应有超时消息, 实测 events={events}"

    # ── worker 注册表清理 ──
    from app.engine import repo_worker as worker_module
    assert terminology_worker_key(repo_id) not in worker_module._active_workers
