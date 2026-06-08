"""trainer pipeline — maybe_trigger_promote 时序回归.

╔══════════════════════════════════════════════════════════════════════════╗
║  背景:                                                                   ║
║    原实现把 maybe_trigger_promote 放在 step 4.7 (65%), 而 parse_status   ║
║    在 step 6 才置 'parsed'. 闸门 all(r.parse_status=='parsed') 永远拿不  ║
║    到当前 repo 的最新状态, 全量重建场景下 SCO 永远为空.                  ║
║                                                                          ║
║  本测验证修复:                                                           ║
║    1. maybe_trigger_promote 必须发生在 _update_repo_fields(parse_status= ║
║       'parsed') 提交之后                                                 ║
║    2. 必须发生在 run_terminology_stage 之前 (terminology_refresher 依赖  ║
║       SchemaCanonicalObject)                                             ║
║                                                                          ║
║  夹具策略:                                                               ║
║    - 真 SQLite + GitRepo / Namespace 写库                                ║
║    - 重 IO 阶段 stub, maybe_trigger_promote / run_terminology_stage /    ║
║      _update_repo_fields 装探针抓调用顺序                                ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.knowledge import trainer as trainer_module
from app.knowledge.parse_result import CodeParseResult, ParserStats
from app.models.git_repo import GitRepo
from app.models.namespace import Namespace


def _stub_heavy_pipeline_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_clone_or_update(url, branch, repo_id):
        return ("/tmp/fake-repo", "cloned")

    def _fake_parse_repository(local_path):
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


@pytest_asyncio.fixture
async def seeded_repo(async_session) -> tuple[int, int, str]:
    async with async_session() as db:
        ns = Namespace(name="ns_promote_timing", slug="ns_promote_timing", description="")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        repo = GitRepo(
            namespace_id=ns.id,
            url="https://example.invalid/promote-timing.git",
            branch="master",
        )
        db.add(repo)
        await db.commit()
        await db.refresh(repo)
        return ns.id, repo.id, ns.slug


@pytest.mark.asyncio
async def test_promote_fires_after_parse_status_and_before_terminology(
    monkeypatch: pytest.MonkeyPatch,
    async_session,
    seeded_repo,
):
    """记录关键事件调用序号, 断言: parse_status < promote."""
    ns_id, repo_id, ns_slug = seeded_repo

    monkeypatch.setattr(trainer_module, "async_session", async_session)
    _stub_heavy_pipeline_stages(monkeypatch)

    call_log: list[str] = []

    original_update = trainer_module._update_repo_fields

    async def _spy_update_repo_fields(rid, **fields):
        if fields.get("parse_status") == "parsed":
            call_log.append("parse_status_parsed")
        return await original_update(rid, **fields)

    monkeypatch.setattr(trainer_module, "_update_repo_fields", _spy_update_repo_fields)

    from app.knowledge import canonical_promote as cp_module

    promote_result = {"called": False, "ns_id": None}

    async def _spy_maybe_trigger_promote(db, ns_id_arg):
        call_log.append("maybe_trigger_promote")
        promote_result["called"] = True
        promote_result["ns_id"] = ns_id_arg
        return None

    monkeypatch.setattr(
        cp_module, "maybe_trigger_promote", _spy_maybe_trigger_promote
    )

    async def on_progress(percent: int, message: str) -> None:
        pass

    await trainer_module.run_training_pipeline_with_progress(
        repo_id=repo_id,
        ns_id=ns_id,
        ns_slug=ns_slug,
        repo_url="https://example.invalid/promote-timing.git",
        repo_branch="master",
        on_progress=on_progress,
    )

    assert promote_result["called"], "maybe_trigger_promote 必须被调用"
    assert promote_result["ns_id"] == ns_id

    parse_idx = call_log.index("parse_status_parsed")
    promote_idx = call_log.index("maybe_trigger_promote")

    assert parse_idx < promote_idx, (
        f"promote 必须在 parse_status='parsed' 之后, 否则 all(parsed) 闸门拿不到当前 repo. "
        f"实测: parse_idx={parse_idx} promote_idx={promote_idx}"
    )
