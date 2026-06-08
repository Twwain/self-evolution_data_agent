"""
evaluator 单元测试 — prompt 构建 + JSON 修复

不调用 LLM, 只测试可独立验证的纯函数逻辑:
- _build_eval_prompt: 按 DB 类型动态切换维度 4
- _parse_eval_json: 截断 JSON 修复
- stats_summary: 包含 query_patterns_trained
"""

from app.knowledge.evaluator import _build_eval_prompt
from app.engine.json_parser import parse_llm_json
from app.knowledge.parse_result import ParseReport, ParserStats


# ════════════════════════════════════════════
#  _build_eval_prompt — 维度 4 动态切换
# ════════════════════════════════════════════

def test_eval_prompt_mongo_only():
    """纯 MongoDB 项目: 维度 4 提及 DAO 查询模式, 不提 MyBatis"""
    report = ParseReport(
        stats=ParserStats(files_scanned=10, files_parsed=5, tables_found=["c_sku"]),
        ddls_trained=0, sqls_trained=0, query_patterns_trained=3, docs_trained=2,
    )
    trained_docs = [
        "MongoDB 集合 c_sku (来自 Sku): 包含字段 ...",
        "MongoDB 集合 c_sku 常见查询模式: ...",
    ]
    prompt = _build_eval_prompt(report, trained_docs)
    assert "MyBatis" not in prompt
    assert "查询模式" in prompt
    assert "查询模式=3" in prompt


def test_eval_prompt_mysql_only():
    """纯 MySQL 项目: 维度 4 提及 MyBatis SQL"""
    report = ParseReport(
        stats=ParserStats(files_scanned=20, files_parsed=10, tables_found=["users"]),
        ddls_trained=5, sqls_trained=8, query_patterns_trained=0, docs_trained=3,
    )
    trained_docs = ["CREATE TABLE users (id BIGINT);"]
    prompt = _build_eval_prompt(report, trained_docs)
    assert "MyBatis" in prompt
    assert "DAO" not in prompt or "查询模式覆盖度" not in prompt


def test_eval_prompt_mixed():
    """混合项目: 维度 4 同时提及 MySQL SQL 和 MongoDB 查询模式"""
    report = ParseReport(
        stats=ParserStats(files_scanned=30, files_parsed=15, tables_found=["users", "c_sku"]),
        ddls_trained=3, sqls_trained=5, query_patterns_trained=2, docs_trained=4,
    )
    trained_docs = [
        "CREATE TABLE users (id BIGINT);",
        "MongoDB 集合 c_sku 常见查询模式: ...",
    ]
    prompt = _build_eval_prompt(report, trained_docs)
    # 混合模式应同时提及
    assert "MySQL" in prompt or "MyBatis" in prompt
    assert "MongoDB" in prompt or "查询模式" in prompt


def test_eval_stats_include_patterns():
    """stats_summary 包含 查询模式=N"""
    report = ParseReport(
        stats=ParserStats(files_scanned=5),
        query_patterns_trained=3,
    )
    prompt = _build_eval_prompt(report, [])
    assert "查询模式=3" in prompt


# ════════════════════════════════════════════
#  _parse_eval_json — 截断修复
# ════════════════════════════════════════════

def test_parse_eval_json_truncated():
    """半截 JSON 补齐后解析成功"""
    truncated = '{"score": 75, "summary": "不错", "unclear_items": [{"category": "schema"'
    result = parse_llm_json(truncated, expect="dict")
    assert result is not None
    assert result["score"] == 75
