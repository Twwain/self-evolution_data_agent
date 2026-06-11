"""Phase 1c Task 1.5 — 4 通道接闸回归 (clarify 在 git 集成流程,单元测不覆盖).

manual / conversation / agent_learn / git_refresh 4 通道全部经过
upsert_terminology_with_validation 闸门. INVALID payload 在每个通道被拒.
"""

import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.knowledge_entry import KnowledgeEntry

VALID = {
    "term": "商品", "primary_collection": "c_category",
    "primary_database": "db_q", "db_type": "mongodb",
    "synonyms": ["货品"], "source_collections": ["c_category"],
}
INVALID = {**VALID, "term": "字段枚举值: 0=draft 1=published"}


# ════════════════════════════════════════════
#  agent_learn 通道 — terminology 经闸门
# ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_save_knowledge_tool_terminology_invalid_returns_failure(
    async_session, seeded_ns_with_mongo_ds,
):
    from app.engine.tools.knowledge_tools import save_knowledge
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ret = await save_knowledge(
            db=db, namespace_id=ns_id, ns_slug="test_ns",
            sse_emit=AsyncMock(),
            entry_type="terminology", content="...", payload=INVALID,
            evidence={"source_trace_id": "t1"},
        )
    assert ret.get("success") is False or "validation" in str(ret.get("reason", "")).lower()


@pytest.mark.asyncio
async def test_save_knowledge_tool_terminology_valid_inserts(
    async_session, seeded_ns_with_mongo_ds,
):
    from app.engine.tools.knowledge_tools import save_knowledge
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ret = await save_knowledge(
            db=db, namespace_id=ns_id, ns_slug="test_ns",
            sse_emit=AsyncMock(),
            entry_type="terminology", content="商品", payload=VALID,
            evidence={"source_trace_id": "t1"},
        )
        await db.commit()
        rows = (await db.execute(select(KnowledgeEntry))).scalars().all()
    assert ret.get("entry_id") is not None
    assert len(rows) == 1
    assert rows[0].entry_type == "terminology"
    assert rows[0].source == "agent_learn"


# ════════════════════════════════════════════
#  agent_learn 通道 — 非 terminology 走原路径不破坏
# ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_save_knowledge_tool_rule_unchanged(
    async_session, seeded_ns_with_mongo_ds,
):
    """save_knowledge entry_type=rule 走原路径,不被 terminology 改造影响."""
    from app.engine.tools.knowledge_tools import save_knowledge
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ret = await save_knowledge(
            db=db, namespace_id=ns_id, ns_slug="test_ns",
            sse_emit=AsyncMock(),
            entry_type="rule", content="按 createdAt 倒排",
            payload={"rule_text": "按 createdAt 倒排"},
            evidence={},
        )
        await db.commit()
        rows = (await db.execute(select(KnowledgeEntry))).scalars().all()
    assert ret.get("entry_id") is not None
    assert len(rows) == 1
    assert rows[0].entry_type == "rule"


# ════════════════════════════════════════════
#  git_refresh 通道 — _upsert_terminology_ke 经闸门
# ════════════════════════════════════════════
@pytest.mark.asyncio
async def test_terminology_refresher_via_gate(
    async_session, seeded_ns_with_mongo_ds, monkeypatch,
):
    """terminology-schema-attribution: _upsert_terminology_ke 改 ns 级签名
    (db, ns_id, t) 去 repo_id 后, INVALID 的 ExtractedTerm 应被拒,
    VALID 应入库 source=schema 且 repo_id IS NULL."""
    ns_id, _repo_id = seeded_ns_with_mongo_ds

    # 把生产 async_session 指向测试会话, 让 _upsert_terminology_ke 内调的
    # `from app.db.metadata import async_session` 用同一份测试 SQLite.
    monkeypatch.setattr("app.db.metadata.async_session", async_session)

    from app.knowledge.terminology_extractor import ExtractedTerm
    from app.knowledge.terminology_refresher import _upsert_terminology_ke

    # VALID: 经闸门后入库 (新签名: 去 repo_id 形参)
    valid_term = ExtractedTerm(
        term="商品",
        synonyms=["货品"],
        primary_collection="c_category",
        primary_database="db_q",
        db_type="mongodb",
        source_collections=["c_category"],
    )
    status_v = await _upsert_terminology_ke(ns_id, valid_term)
    assert status_v == "inserted"

    # INVALID: term 含分号 → 闸门拒, 返 'failed' or 'skipped'
    bad_term = ExtractedTerm(
        term="字段枚举值: 0=draft; 1=published",
        synonyms=["x"],
        primary_collection="c_category",
        primary_database="db_q",
        db_type="mongodb",
        source_collections=["c_category"],
    )
    status_i = await _upsert_terminology_ke(ns_id, bad_term)
    assert status_i in ("failed", "skipped")

    async with async_session() as db:
        rows = (await db.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.namespace_id == ns_id)
        )).scalars().all()
    terms = [json.loads(r.payload).get("term") for r in rows]
    assert "商品" in terms
    assert "字段枚举值: 0=draft; 1=published" not in terms
    # 术语只归属 schema/namespace: source=schema, repo_id IS NULL
    written = [r for r in rows if json.loads(r.payload).get("term") == "商品"]
    assert written and all(r.source == "schema" for r in written)
    assert all(r.repo_id is None for r in written)
