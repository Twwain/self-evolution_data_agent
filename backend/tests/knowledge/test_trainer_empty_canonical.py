"""Phase 2 Task 2.4 — 空 canonical 边缘场景兜底.

╔══════════════════════════════════════════════════════════════════════════╗
║  目标:                                                                   ║
║    namespace 内无 SchemaCanonicalObject(db_type='mongodb') 时           ║
║    (refresh_terms_for_repo 返回 RefreshReport(skipped=True,             ║
║    reason="no_canonicals")), pipeline                                    ║
║    在 99% 进度发出友好中文消息 ("无业务术语" 关键词), 末态保持           ║
║    (100, "完成"), GitRepo.parse_status='parsed' / term_refresh_status=   ║
║    'skipped'.                                                            ║
║                                                                          ║
║  夹具策略 (按 §06 — 不 mock 知识层真实数据):                             ║
║    - SQLite + GitRepo / Namespace 真实写库 (async_session fixture)       ║
║    - 重 IO 阶段 (clone/parse/build/train/evaluate) 与 refresh_terms_     ║
║      for_repo 本身打桩, on_progress 走真 callable 收事件                 ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.knowledge import trainer as trainer_module
from app.knowledge import trainer_terminology_stage as term_stage_module
from app.knowledge.parse_result import CodeParseResult, ParseReport, ParserStats
from app.knowledge.terminology_extractor import RefreshReport
from app.models.git_repo import GitRepo
from app.models.namespace import Namespace


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers — 与 test_trainer_terminology_stage.py 保持同形态
# ══════════════════════════════════════════════════════════════════════════════

def _stub_heavy_pipeline_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub clone/parse/build/train/evaluate — 让流水线秒走完."""

    def _fake_clone_or_update(url: str, branch: str, repo_id: int):
        return ("/tmp/fake-repo", "cloned")

    def _fake_parse_repository(local_path: str):
        return CodeParseResult(), ParserStats()

    def _fake_build_docs(code_result):
        return ([], [])

    def _fake_evaluate(report, all_trained):
        report.completeness_score = 100
        report.unclear_items = []
        return report

    monkeypatch.setattr(trainer_module, "clone_or_update", _fake_clone_or_update)
    monkeypatch.setattr(trainer_module, "parse_repository", _fake_parse_repository)
    monkeypatch.setattr(trainer_module, "_build_docs", _fake_build_docs)
    monkeypatch.setattr(trainer_module, "evaluate_parse_quality", _fake_evaluate)


def _patch_async_session(monkeypatch: pytest.MonkeyPatch, factory) -> None:
    """让 trainer / term_stage 内部 async_session 走测试 SQLite."""
    monkeypatch.setattr(trainer_module, "async_session", factory)
    monkeypatch.setattr(term_stage_module, "async_session", factory)


@pytest_asyncio.fixture
async def seeded_repo_for_terminology(async_session) -> tuple[int, int, str]:
    """ns + repo, 返回 (ns_id, repo_id, ns_slug)."""
    async with async_session() as db:
        ns = Namespace(name="ns_term_empty", slug="ns_term_empty", description="phase2-empty")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        repo = GitRepo(
            namespace_id=ns.id,
            url="https://example.invalid/empty-canon.git",
            branch="master",
        )
        db.add(repo)
        await db.commit()
        await db.refresh(repo)
        return ns.id, repo.id, ns.slug


# ══════════════════════════════════════════════════════════════════════════════
#  Test — 空 canonical 走 skipped 分支, 99% 进度消息含"无业务术语" 关键词
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_empty_canonical_skips_with_friendly_message(
    monkeypatch: pytest.MonkeyPatch,
    async_session,
    seeded_repo_for_terminology,
):
    """空 canonical → terminology stage skipped, 消息中文友好可读, pipeline 末态 (100,完成)."""
    ns_id, repo_id, ns_slug = seeded_repo_for_terminology

    _patch_async_session(monkeypatch, async_session)
    _stub_heavy_pipeline_stages(monkeypatch)

    # ── refresh 探针: 模拟空 canonical 场景 ──
    seen_args: list[tuple[Any, ...]] = []

    async def _empty_canonical_refresh(db, ns_id_arg, repo_id_arg):
        seen_args.append((ns_id_arg, repo_id_arg))
        return RefreshReport(skipped=True, reason="no_canonicals")

    monkeypatch.setattr(term_stage_module, "refresh_terms_for_repo", _empty_canonical_refresh)

    events: list[tuple[int, str]] = []

    async def on_progress(percent: int, message: str) -> None:
        events.append((percent, message))

    report = await trainer_module.run_training_pipeline_with_progress(
        repo_id=repo_id,
        ns_id=ns_id,
        ns_slug=ns_slug,
        repo_url="https://example.invalid/empty-canon.git",
        repo_branch="master",
        on_progress=on_progress,
    )

    assert isinstance(report, ParseReport)
    assert seen_args == [(ns_id, repo_id)], "refresh_terms_for_repo 应被调一次"

    # ── 末态 (100, "完成") 保留 ──
    assert events[-1] == (100, "完成"), f"末态错误: {events[-1]}"

    # ── 99% skipped 事件存在且消息中文友好 ──
    skipped_events = [(p, m) for p, m in events if p == 99]
    assert skipped_events, f"应有 99% 进度事件, 实测 events={events}"
    assert any("无业务术语" in m for _, m in skipped_events), \
        f"99% 消息应含'无业务术语', 实测 {[m for _, m in skipped_events]}"

    # ── 不应包含原始英文 reason 关键词 (友好性回归保护) ──
    assert not any("no_canonicals" in m for _, m in skipped_events), \
        f"99% 消息不应暴露原始 reason 'no_canonicals', 实测 {[m for _, m in skipped_events]}"

    # ── GitRepo 状态: parse_status=parsed / term_refresh_status=skipped ──
    async with async_session() as db:
        row = await db.scalar(select(GitRepo).where(GitRepo.id == repo_id))
        assert row is not None
        assert row.parse_status == "parsed", \
            f"parse_status 应为 parsed, 实测 {row.parse_status}"
        assert row.term_refresh_status == "skipped", \
            f"term_refresh_status 应为 skipped, 实测 {row.term_refresh_status}"
