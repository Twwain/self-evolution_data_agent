"""补充单测 — terminology-schema-attribution (task 10).

聚焦探索性测试 (Property 1-8) 未直接覆盖的函数级行为, 不与其重复:

  - `_run_refresh` 无 canonical → skipped 友好消息 (gate 探索只覆盖"有 canonical
    无 repo → 执行抽取"分支, 未覆盖 no_canonicals skipped 文案)
  - `_create_proposed` source=schema → audit action="auto_generate"
    (source 标签探索只验证 KE.source, 未验证 audit action 分支)
  - `extraction_writer._write_terminology_ke` repo_id 不写入
    (source 标签探索 3c 只验证 KE.source=schema, 未验证 repo_id IS NULL)

这些用例走 conftest 的 SAVEPOINT-rollback `async_session`/`db_session`
(被测函数仅 flush 不 self-commit, 与回滚隔离兼容).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import DataSource, Namespace
from app.schemas.knowledge_payload import TerminologyPayload

# ════════════════════════════════════════════════════════════════
#  _run_refresh — 无 canonical → skipped 友好消息
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_run_refresh_no_canonical_emits_friendly_skipped_message(
    async_session, monkeypatch,
):
    """无 canonical: refresh_namespace_terminology 返回 skipped=True/no_canonicals,
    _run_refresh SHALL 落 completed + 友好中文消息 + result.reason=no_canonicals.

    与 gate 探索测试 (有 canonical 无 repo → 执行抽取) 互补: 本用例钉死
    "闸门基于 canonical 缺失而跳过" 的文案与 result 结构.
    """
    from app.api import terminology_refresh as refresh_module
    from app.knowledge.terminology_extractor import RefreshReport

    monkeypatch.setattr(refresh_module, "async_session", async_session)

    # 旁路清场 (无关本属性)
    async def _noop_purge(*args, **kwargs):
        return 0

    monkeypatch.setattr(refresh_module, "_purge_schema_terminology", _noop_purge)

    # 抽词入口返回 no_canonicals skipped
    async def _skip_refresh(db, ns_id_arg):
        return RefreshReport(skipped=True, reason="no_canonicals")

    monkeypatch.setattr(refresh_module, "refresh_namespace_terminology", _skip_refresh)

    task_id = uuid.uuid4().hex[:12]
    refresh_module._refresh_tasks[task_id] = {
        "ns_id": 12345,
        "status": "running",
        "progress": 0,
        "message": "清除历史术语...",
    }

    await refresh_module._run_refresh(task_id, 12345, "ns_no_canon")

    info = refresh_module._refresh_tasks[task_id]
    assert info["status"] == "completed"
    assert info["message"] == "无业务术语数据(无 canonical), 跳过抽取"
    assert info["result"]["inserted"] == 0
    assert info["result"]["failed"] == 0
    assert info["result"]["reason"] == "no_canonicals"

    # 清理进程内任务表
    refresh_module._refresh_tasks.pop(task_id, None)


# ════════════════════════════════════════════════════════════════
#  _create_proposed — source=schema → audit action="auto_generate"
# ════════════════════════════════════════════════════════════════


async def _seed_ns(db) -> int:
    uid = uuid.uuid4().hex[:8]
    ns = Namespace(name=f"unit_{uid}", slug=f"unit_{uid}", description="task10 单测")
    db.add(ns)
    await db.flush()
    return ns.id


def _payload(term: str = "商品") -> TerminologyPayload:
    return TerminologyPayload(
        term=term,
        primary_collection="c_category",
        primary_database="db_mysql",
        db_type="mysql",
        synonyms=["货品"],
        source_collections=["c_category"],
    )


@pytest.mark.asyncio
async def test_create_proposed_schema_source_uses_auto_generate_action(db_session):
    """source=schema → audit action="auto_generate" + KE.source=schema + repo_id NULL.

    schema 自省抽词与 git 抽词同属系统自动批量产出, 审计语义为 auto_generate.
    """
    from app.knowledge.terminology_intake import _create_proposed

    ns_id = await _seed_ns(db_session)

    ke = await _create_proposed(
        db_session, ns_id, _payload("商品"), "schema",
        repo_id=None, raw_input="", evidence=None,
    )

    assert ke.source == "schema"
    assert ke.repo_id is None

    action = (await db_session.execute(
        select(KnowledgeAuditLog.action).where(KnowledgeAuditLog.entry_id == ke.id)
    )).scalar_one()
    assert action == "auto_generate"


@pytest.mark.asyncio
async def test_create_proposed_manual_source_uses_propose_action(db_session):
    """对照: source=manual → action="propose" (确认 schema 走 auto_generate 不是巧合)."""
    from app.knowledge.terminology_intake import _create_proposed

    ns_id = await _seed_ns(db_session)

    ke = await _create_proposed(
        db_session, ns_id, _payload("订单"), "manual",
        repo_id=None, raw_input="", evidence=None,
    )

    action = (await db_session.execute(
        select(KnowledgeAuditLog.action).where(KnowledgeAuditLog.entry_id == ke.id)
    )).scalar_one()
    assert action == "propose"


# ════════════════════════════════════════════════════════════════
#  extraction_writer._write_terminology_ke — repo_id 不写入
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_extraction_writer_terminology_ke_repo_id_not_written(db_session):
    """潜伏路径 _write_terminology_ke: 即便调用方传 repo_id, 术语 KE 也不写入 repo_id.

    与 source 标签探索 3c 互补: 3c 只验证 source=schema, 本用例钉死 repo_id IS NULL
    (术语只归属 schema/namespace, 彻底移除 repo_id 归属).
    """
    from app.knowledge import extraction_writer

    ns_id = await _seed_ns(db_session)
    # _write_terminology_ke → upsert 闸门需 db_type 一致性: 备 DataSource
    db_session.add(DataSource(
        namespace_id=ns_id, db_type="mysql", database="db_mysql",
        host="localhost", port=3306, username="", password="",
    ))
    await db_session.flush()

    term = {
        "term": "潜伏实体",
        "primary_collection": "c_writer",
        "primary_database": "db_mysql",
        "db_type": "mysql",
        "synonyms": ["writer_syn"],
        "source_collections": ["c_writer"],
    }

    created = await extraction_writer._write_terminology_ke(
        db_session, ns_id, repo_id=999, term=term, repo_name="writer",
    )
    await db_session.flush()
    assert created, "术语 KE 未写入 — 无法验证 repo_id 归属"

    rows = (await db_session.execute(
        select(KnowledgeEntry.repo_id, KnowledgeEntry.source).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.entry_type == "terminology",
        )
    )).all()
    assert rows, "未查到术语 KE"
    offenders = [(rid, src) for rid, src in rows if rid is not None or src != "schema"]
    assert not offenders, (
        "术语 KE 被挂 repo_id 或 source!=schema (传入 repo_id=999 应被丢弃, "
        f"术语只归属 schema). 违例 [(repo_id, source), ...]={offenders}"
    )
