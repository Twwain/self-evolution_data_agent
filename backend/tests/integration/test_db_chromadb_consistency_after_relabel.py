"""Phase 0 Task 0.3 — relabel 后 DB ↔ ChromaDB 一致性回归.

验证 commit `b35d048` 的"commit-then-delete"路径: relabel_one_batch 真改 entry_type
后, ChromaDB 旧向量被 delete_knowledge_entry 清除, 不留孤儿; 同时 DB 与
ChromaDB 视图不出现 type_mismatch.
"""

import pytest

from scripts.relabel_legacy_terminology import relabel_one_batch
from scripts.verify_db_chromadb_consistency import diff_db_vs_chromadb


@pytest.fixture
def stub_llm_classify(monkeypatch):
    """按 payload.term 关键字模拟 LLM 分类, 替换真 LLM 调用. 与 tests/scripts 复用."""

    async def fake(term: str, content: str) -> str | None:
        t = (term or "") + " " + (content or "")
        if "枚举" in t or "0=" in t:
            return "schema_summary"
        if "关联" in t or "一对多" in t or "FK" in t:
            return "rule"
        return "terminology"

    monkeypatch.setattr(
        "scripts.relabel_legacy_terminology._llm_classify", fake
    )
    return fake


@pytest.mark.asyncio
@pytest.mark.usefixtures("real_chromadb", "stub_llm_classify")
async def test_relabel_run_then_chromadb_diff_zero(
    async_session, seeded_legacy_terminology_kes
):
    """跑 relabel, 再扫一致性 — chromadb_only 与 type_mismatch 必须清零."""
    async with async_session() as db:
        await relabel_one_batch(db, seeded_legacy_terminology_kes)

    async with async_session() as db:
        diff = await diff_db_vs_chromadb(db)

    assert diff["chromadb_only"] == [], (
        f"relabel 后 ChromaDB 不应残留旧 terminology 向量, actual: {diff}"
    )
    assert diff["type_mismatch"] == [], (
        f"relabel 后 DB ↔ ChromaDB entry_type 不应错配, actual: {diff}"
    )
    # db_only 可能非空 (KE 仍在 DB 但 status=proposed 不会触发 verify 扫描),
    # 这里冗余断言用于回归 verify 函数对 status 过滤的契约.
    assert diff["db_only"] == [], (
        f"非 canonical KE 不应出现在 db_only, actual: {diff}"
    )
