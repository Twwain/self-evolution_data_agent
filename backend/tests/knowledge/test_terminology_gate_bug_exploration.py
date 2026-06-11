"""Bug condition exploration test — 术语抽取闸门错配 (Property 1).

**Validates: Requirements 2.1, 2.2**

═══════════════════════════════════════════════════════════════════════════════
  Property 1: Bug Condition — 闸门基于 canonical 存在性
═══════════════════════════════════════════════════════════════════════════════

  isBugCondition_gate(NS):
      count_canonicals_with_description(NS) > 0  AND
      count_parsed_git_repos(NS) == 0

  期望(修复后)行为:
      对满足 isBugCondition_gate 的 NS, _run_refresh SHALL 基于 canonical 存在性
      执行实际抽取 (skipped=false, 进入抽词内核), 而非整体跳过.

  **CRITICAL**: 本测试在未修复代码上 *预期 FAIL* —— 失败即确认 bug 存在.
  未修复的 `_run_refresh` 用 `GitRepo.parse_status=="parsed"` 作前置闸门,
  无 parsed repo 时整体跳过, 返回 "无已解析的仓库, 跳过术语提取"、inserted=0,
  从未进入抽词内核.

  Scoped PBT: 闸门是确定性逻辑, 收敛到具体失败用例 —— 构造
  (canonical 带 description=1, parsed GitRepo=0) 这一最小 bug 条件场景.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.api import terminology_refresh as refresh_module
from app.knowledge.terminology_extractor import RefreshReport
from app.models.namespace import DataSource, Namespace
from app.models.schema_canonical_object import SchemaCanonicalObject


# ════════════════════════════════════════════════════════════════
#  Fixture — bug condition NS: canonical 带 description=1, parsed GitRepo=0
#  (复刻 ns36 new_energy: 直连自省建库, 无 git 仓库)
# ════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def bug_condition_ns(async_session) -> tuple[int, str, object]:
    """ns + mongodb DataSource + 1 canonical(带 description), 无任何 parsed GitRepo.

    返回 (ns_id, ns_slug, async_session_factory).
    isBugCondition_gate(NS) == True.
    """
    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(
            name=f"gate_bug_{uid}", slug=f"gate_bug_{uid}",
            description="闸门错配复现 — 无 git repo 直连自省建库",
        )
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        ds = DataSource(
            namespace_id=ns.id, db_type="mongodb", database="db_energy",
            host="localhost", port=27017, username="", password="",
        )
        db.add(ds)

        # 可抽词 canonical (description 非空) — 真相源
        db.add(SchemaCanonicalObject(
            namespace_id=ns.id,
            db_type="mongodb",
            database="db_energy",
            target="c_station",
            description="充电站基础信息",
            purpose_detail="记录每个充电站的位置、容量与运营状态",
        ))
        await db.commit()
        # 注意: 不创建任何 GitRepo → count_parsed_git_repos == 0
        return ns.id, ns.slug, async_session


# ════════════════════════════════════════════════════════════════
#  Property 1 — 闸门基于 canonical 存在性 (未修复代码上预期 FAIL)
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_gate_extracts_when_canonical_exists_without_parsed_repo(
    bug_condition_ns: tuple[int, str, object],
    monkeypatch: pytest.MonkeyPatch,
):
    """satisfies isBugCondition_gate(NS) → _run_refresh 应执行实际抽取, 不整体跳过.

    EXPECTED OUTCOME on unfixed code: FAIL —— 抽词内核从未被调用, 返回
    "无已解析的仓库, 跳过术语提取"、inserted=0.
    """
    ns_id, ns_slug, session_factory = bug_condition_ns

    # ── 让 _run_refresh 内部 `async with async_session()` 走测试 SQLite/PG ──
    monkeypatch.setattr(refresh_module, "async_session", session_factory)

    # ── 抽词内核探针: 记录是否被调用 + 返伪 RefreshReport(实际抽取, 未跳过) ──
    kernel_calls: list[tuple] = []

    async def _probe_kernel(db, ns_id_arg, *args):
        kernel_calls.append((ns_id_arg, args))
        return RefreshReport(
            canonicals_seen=1,
            merged=["充电站"],
            failed=[],
            skipped=False,
        )

    # 未修复代码调 refresh_terms_for_repo; 修复后调 refresh_namespace_terminology.
    # 两个名字都打桩, 使测试在修复前后均观察同一抽词内核入口.
    monkeypatch.setattr(
        refresh_module, "refresh_terms_for_repo", _probe_kernel, raising=False,
    )
    monkeypatch.setattr(
        refresh_module, "refresh_namespace_terminology", _probe_kernel, raising=False,
    )

    # ── 旁路清场(无关本属性), 避免触碰 ChromaDB ──
    async def _noop_purge(*args, **kwargs):
        return 0

    monkeypatch.setattr(
        refresh_module, "_purge_git_terminology", _noop_purge, raising=False,
    )
    monkeypatch.setattr(
        refresh_module, "_purge_schema_terminology", _noop_purge, raising=False,
    )

    # ── 注册任务并执行后台刷新主体 ──
    task_id = uuid.uuid4().hex[:12]
    refresh_module._refresh_tasks[task_id] = {
        "ns_id": ns_id,
        "status": "running",
        "progress": 0,
        "message": "清除历史术语...",
    }

    await refresh_module._run_refresh(task_id, ns_id, ns_slug)

    info = refresh_module._refresh_tasks[task_id]

    # ── 期望行为(修复后): 抽词内核被调用, 基于 canonical 执行实际抽取 ──
    assert kernel_calls, (
        "抽词内核未被调用 —— 闸门基于 parsed GitRepo 存在性整体跳过了抽取. "
        f"info={info}"
    )
    assert info.get("message") != "无已解析的仓库, 跳过术语提取", (
        "_run_refresh 因无 parsed repo 整体跳过术语提取 (闸门错配 bug). "
        f"实测 message={info.get('message')!r}"
    )
    assert info.get("result", {}).get("inserted", 0) > 0, (
        "术语抽取被跳过, inserted=0 —— 闸门应基于 canonical 存在性执行抽取. "
        f"实测 result={info.get('result')}"
    )
