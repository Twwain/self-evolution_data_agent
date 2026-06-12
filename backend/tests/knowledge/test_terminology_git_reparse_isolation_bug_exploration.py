"""Bug condition / fix checking exploration test — git_reparse 不删 schema 术语 (Property 8).

**Validates: Requirements 2.9, 3.3**

═══════════════════════════════════════════════════════════════════════════════
  Property 8: Bug Condition / Fix Checking — git_reparse 不再删除 schema 术语
═══════════════════════════════════════════════════════════════════════════════

  对任意 git_reparse 清场 (`_clean_namespace_knowledge_entries`,
  scope `source ∈ ["git","self_answer","clarify"]`) 在某 namespace 上执行:
      SHALL 保留该 ns 下 `source="schema"` 的术语 KE 完整不删
      (术语脱离 git_reparse scope);
      同时 SHALL CONTINUE TO 删除该 scope 内的非术语条目
      (example / route_hint / rule 等).

  此为 *有意行为变更*（非回归）：术语清场归属手动刷新 `_purge_schema_terminology`
  与 trainer 全量重建 `_delete_schema_terminology` 两条 schema 专用路径。

  **CRITICAL**: 本测试钉死 *有意行为变更点*。
  未修复代码上 *预期 FAIL* —— 失败即确认行为变更点。
  未修复代码中术语 source=git，落入 git_reparse scope ["git","self_answer","clarify"]
  被删，故「术语保留」断言失败。

  **NOTE / 依赖记录**: 未修复代码的 Source Literal 尚无 "schema" 成员
  (`terminology_intake.Source`)，故无法经闸门写入路径构造 source="schema" 的术语。
  但 git_reparse 的 scope 是 *source 字符串* 过滤 (BulkOperationGuard.scope_filter)，
  与 Source Literal 校验无关。本测试用 ORM 直写构造 source="schema" 术语 KE
  (ORM 直写绕过 intake 的 Literal 校验)。该测试以「断言期望(修复后)行为」形式编写，
  随 task 9.1 (Source 加 schema 成员) + 9.10 转入正式 PASS 语义；本阶段在未修复
  代码上验证术语写入路径仍打 source=git → 落 scope 被删，确认行为变更点。

  场景构造 (同一 ns):
    - example     (source="git")        ← git_reparse scope 命中, 应被删
    - route_hint  (source="self_answer") ← git_reparse scope 命中, 应被删
    - rule        (source="clarify")     ← git_reparse scope 命中, 应被删
    - terminology (source="schema", repo_id=NULL) ← 脱离 scope, 修复后应保留
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


# ════════════════════════════════════════════════════════════════
#  本地 async_session — 真实引擎 + sessionmaker.
#  不能复用 knowledge/conftest 的 SAVEPOINT-rollback async_session:
#  _clean_namespace_knowledge_entries → BulkOperationGuard.execute 内部
#  自行 db.commit(), 与 SAVEPOINT rollback 隔离的 _restart_savepoint 事件冲突.
#  本 fixture 走真实事务 + 末尾按 ns_id 显式清理 (CASCADE 删 KE/audit).
# ════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def async_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    created_ns_ids: list[int] = []
    factory._created_ns_ids = created_ns_ids  # type: ignore[attr-defined]
    yield factory

    # ── teardown: 删测试创建的 ns (CASCADE 清 KE / audit) ──
    if created_ns_ids:
        async with factory() as db:
            await db.execute(
                delete(Namespace).where(Namespace.id.in_(created_ns_ids))
            )
            await db.commit()
    await engine.dispose()


def _mk_ke(ns_id, *, entry_type, source, content, repo_id=None):
    return KnowledgeEntry(
        namespace_id=ns_id,
        entry_type=entry_type,
        status="proposed",
        tier="normal",
        content=content,
        payload="{}",
        source=source,
        repo_id=repo_id,
        is_superseded=False,
        raw_input="",
        evidence_json="{}",
    )


# ════════════════════════════════════════════════════════════════
#  Property 8 — git_reparse 清场保留 schema 术语、删 scope 内非术语
#  (未修复代码上预期 FAIL: 术语此时 source=git → 落 scope 被删)
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_git_reparse_preserves_schema_terminology(
    async_session, chroma_isolated,
):
    """git_reparse 清场 SHALL 保留 source="schema" 术语 KE, 删 scope 内非术语条目.

    EXPECTED OUTCOME on unfixed code: FAIL —— 术语写入路径硬编码 source="git",
    落入 git_reparse scope ["git","self_answer","clarify"] 被删, 「术语保留」断言失败.
    本测试以 ORM 直写构造 source="schema" 术语 (绕过 intake Source Literal 校验),
    断言期望(修复后)行为: 术语脱离 scope 完整保留.
    """
    from app.api.knowledge import _clean_namespace_knowledge_entries

    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(
            name=f"greparse_{uid}", slug=f"greparse_{uid}",
            description="git_reparse 术语隔离复现",
        )
        db.add(ns)
        await db.commit()
        await db.refresh(ns)
        async_session._created_ns_ids.append(ns.id)  # teardown 清理

        # ── git_reparse scope 命中目标 (非术语, source ∈ git/self_answer/clarify) ──
        example_ke = _mk_ke(
            ns.id, entry_type="example", source="git",
            content="git example",
        )
        route_hint_ke = _mk_ke(
            ns.id, entry_type="route_hint", source="self_answer",
            content="self_answer route_hint",
        )
        rule_ke = _mk_ke(
            ns.id, entry_type="rule", source="clarify",
            content="clarify rule",
        )
        # ── 修复目标: 术语 (source="schema", repo_id=NULL) 脱离 scope, 应保留 ──
        term_ke = _mk_ke(
            ns.id, entry_type="terminology", source="schema",
            content="schema terminology", repo_id=None,
        )
        db.add_all([example_ke, route_hint_ke, rule_ke, term_ke])
        await db.commit()
        await db.refresh(example_ke)
        await db.refresh(route_hint_ke)
        await db.refresh(rule_ke)
        await db.refresh(term_ke)

        example_id = example_ke.id
        route_hint_id = route_hint_ke.id
        rule_id = rule_ke.id
        term_id = term_ke.id

        await _clean_namespace_knowledge_entries(db, ns.id, ns.slug, actor_id=None)
        await db.commit()

    # ── 验证 ──
    async with async_session() as db:
        # scope 内非术语条目被清 (基线, 不变)
        assert await db.get(KnowledgeEntry, example_id) is None, (
            "scope 内 example(source=git) 应被 git_reparse 清场删除"
        )
        assert await db.get(KnowledgeEntry, route_hint_id) is None, (
            "scope 内 route_hint(source=self_answer) 应被 git_reparse 清场删除"
        )
        assert await db.get(KnowledgeEntry, rule_id) is None, (
            "scope 内 rule(source=clarify) 应被 git_reparse 清场删除"
        )

        # source="schema" 术语 KE 完整保留 (修复目标 —— 未修复代码上此断言 FAIL)
        surviving_term = await db.get(KnowledgeEntry, term_id)
        assert surviving_term is not None, (
            "source=schema 术语 KE 应脱离 git_reparse scope ["
            "git,self_answer,clarify] 被完整保留 (有意行为变更). "
            "未修复代码中术语写入路径硬编码 source=git, 落入 scope 被删. "
            f"术语 KE id={term_id} 被错误删除."
        )
        assert surviving_term.source == "schema", (
            f"保留的术语 KE source 应为 schema, 实际 {surviving_term.source!r}"
        )
