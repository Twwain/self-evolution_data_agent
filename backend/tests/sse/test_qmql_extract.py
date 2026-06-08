# backend/tests/sse/test_qmql_extract.py
"""Q-MQL 提取后台脚本测试 — TDD 红/绿/重构"""
import json
import pytest
from unittest.mock import patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QueryHistory, KnowledgeEntry


# ────────────────────────────────────────────────────────────────────────────
#  _find_extractable_rows: 只返回成功行 (row_count > 0 && error 为空 && generated_query 非空)
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_find_extractable_rows_filters_error_rows(db_session, test_namespace):
    from scripts.extract_query_examples_from_history import _find_extractable_rows

    db_session.add(QueryHistory(
        namespace_id=test_namespace.id, session_id="s1", role="user",
        content="total users?",
        generated_query='{"collection":"users","pipeline":[{"$count":"n"}]}',
        row_count=1, error="", result_snapshot='{}',
    ))
    db_session.add(QueryHistory(
        namespace_id=test_namespace.id, session_id="s2", role="user",
        content="bad query", generated_query="", row_count=0, error="timeout",
        result_snapshot="",
    ))
    await db_session.commit()

    rows = await _find_extractable_rows(db_session, limit=10, min_age_hours=0,
                                         namespace_id=test_namespace.id)
    assert len(rows) == 1
    assert rows[0].content == "total users?"


# ────────────────────────────────────────────────────────────────────────────
#  _already_extracted: 无重复 KE 时返 False
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_already_extracted_false_when_no_ke(db_session, test_namespace):
    from scripts.extract_query_examples_from_history import _already_extracted

    result = await _already_extracted(db_session, test_namespace.id, "new question")
    assert result is False


# ────────────────────────────────────────────────────────────────────────────
#  _already_extracted: 重复 KE 存在时返 True
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_already_extracted_true_when_ke_exists(db_session, test_namespace):
    from scripts.extract_query_examples_from_history import _already_extracted

    db_session.add(KnowledgeEntry(
        namespace_id=test_namespace.id, content="total users?",
        entry_type="example", tier="normal", status="proposed",
        source="qmql_extract", description="total users?",
    ))
    await db_session.commit()
    result = await _already_extracted(db_session, test_namespace.id, "total users?")
    assert result is True


# ────────────────────────────────────────────────────────────────────────────
#  _refine_to_example_payload: 校验 ExamplePayload 字段结构
#  真实字段: question / target_collection / target_database / query_json /
#            result_summary / source_query_history_id / schema_hash
# ────────────────────────────────────────────────────────────────────────────
def test_refine_to_example_payload_structure():
    from scripts.extract_query_examples_from_history import _refine_to_example_payload

    raw = '{"collection": "users", "pipeline": [{"$count": "n"}]}'
    llm_response = json.dumps({
        "question": "total users?",
        "target_collection": "users",
        "target_database": None,
        "query_json": {"collection": "users", "pipeline": [{"$count": "n"}]},
        "result_summary": "1 row",
        "source_query_history_id": None,
        "schema_hash": None,
    })
    with patch("scripts.extract_query_examples_from_history.chat_completion", return_value=llm_response):
        payload = _refine_to_example_payload("total users?", raw)

    assert payload.question == "total users?"
    assert payload.target_collection == "users"
    assert payload.result_summary == "1 row"


# ────────────────────────────────────────────────────────────────────────────
#  run_extraction dry-run: dry_run=True 时不写 KE
#  通过 _db_override 注入测试 session, 避免 async_session() 连生产 DB
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_extraction_dry_run_writes_nothing(db_session, test_namespace):
    from scripts.extract_query_examples_from_history import run_extraction

    db_session.add(QueryHistory(
        namespace_id=test_namespace.id, session_id="s3", role="user",
        content="count events?",
        generated_query='{"collection":"events","pipeline":[]}',
        row_count=5, error="", result_snapshot='{}',
    ))
    await db_session.commit()

    llm_resp = json.dumps({
        "question": "count events?",
        "target_collection": "events",
        "target_database": None,
        "query_json": {},
        "result_summary": "5 rows",
        "source_query_history_id": None,
        "schema_hash": None,
    })
    with patch("scripts.extract_query_examples_from_history.chat_completion", return_value=llm_resp), \
         patch("scripts.extract_query_examples_from_history.settings") as mock_settings:
        mock_settings.qmql_extract_max_per_run = 50
        mock_settings.qmql_extract_min_success_age_hours = 0  # 让刚插入的行也通过
        stats = await run_extraction(dry_run=True, _db_override=db_session)

    assert stats["written"] == 0  # dry-run: 不写入
    assert stats["scanned"] >= 1
