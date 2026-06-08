"""
code_parser 单元测试 — 预筛关键词 + 查询模式校验 + 集合名解析

不调用 LLM, 只测试可独立验证的纯函数逻辑
"""

import os
import tempfile

from app.knowledge.code_parser import (
    _JAVA_KEYWORDS,
    _pre_filter,
    _validate_mongo_query_patterns,
)
from app.knowledge.parse_result import CodeParseResult


# ════════════════════════════════════════════
#  预筛关键词 — DAO/Repository 文件命中
# ════════════════════════════════════════════

def _write_temp_java(content: str) -> str:
    """写临时 .java 文件, 返回路径"""
    f = tempfile.NamedTemporaryFile(suffix=".java", mode="w", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def test_pre_filter_matches_dao_keywords():
    """DAO 关键词 (CrudRepository/MongoTemplate/@Repository/@Document) 全部命中"""
    files = [
        _write_temp_java("public interface SkuRepo extends CrudRepository<Q, String> {}"),
        _write_temp_java("@Repository\npublic class TaskDao { MongoTemplate mt; }"),
        _write_temp_java("@Document(collection=\"c_word\")\npublic class Word {}"),
        _write_temp_java("public interface ProductRepo extends MongoRepository<P, String> {}"),
    ]
    try:
        relevant, skipped = _pre_filter(files, _JAVA_KEYWORDS)
        assert len(relevant) == 4
        assert len(skipped) == 0
    finally:
        for f in files:
            os.unlink(f)


def test_pre_filter_skips_irrelevant():
    """纯 Service 文件无数据库关键词, 应被跳过"""
    files = [
        _write_temp_java("@Service\npublic class MyService { void run() {} }"),
    ]
    try:
        relevant, skipped = _pre_filter(files, _JAVA_KEYWORDS)
        assert len(relevant) == 0
        assert len(skipped) == 1
    finally:
        for f in files:
            os.unlink(f)


# ════════════════════════════════════════════
#  _validate_mongo_query_patterns
# ════════════════════════════════════════════

def test_validate_mongo_query_patterns_valid():
    """合法 pattern 无校验错误"""
    patterns = [{
        "collection": "c_sku",
        "method": "findByStatus",
        "pattern_type": "repository_method",
        "fields_used": ["status"],
    }]
    errors = _validate_mongo_query_patterns(patterns)
    assert errors == []


def test_validate_mongo_query_patterns_missing_collection():
    """缺少 collection 字段报错"""
    patterns = [{
        "method": "findAll",
        "pattern_type": "repository_method",
    }]
    errors = _validate_mongo_query_patterns(patterns)
    assert len(errors) == 1
    assert "collection" in errors[0]


def test_validate_mongo_query_patterns_missing_method():
    """缺少 method 字段报错"""
    patterns = [{
        "collection": "c_sku",
        "pattern_type": "repository_method",
    }]
    errors = _validate_mongo_query_patterns(patterns)
    assert len(errors) == 1
    assert "method" in errors[0]


# ════════════════════════════════════════════
#  集合名解析 (类名 → 实际集合名)
# ════════════════════════════════════════════

def test_collection_name_resolution():
    """pattern 中的类名通过 mongo_documents 映射为实际集合名"""
    result = CodeParseResult(
        mongo_documents=[
            {"class_name": "WordEntity", "collection": "c_word", "fields": []},
            {"class_name": "Sku", "collection": "c_sku", "fields": []},
        ],
        mongo_query_patterns=[
            {
                "collection": "WordEntity",
                "method": "findAll",
                "pattern_type": "repository_method",
                "fields_used": [],
            },
            {
                "collection": "c_sku",  # 已经是集合名, 不需要映射
                "method": "findByStatus",
                "pattern_type": "repository_method",
                "fields_used": ["status"],
            },
        ],
    )
    # 模拟 parse_repository 中的后处理逻辑
    class_to_coll = {d["class_name"]: d["collection"] for d in result.mongo_documents}
    for p in result.mongo_query_patterns:
        if p["collection"] in class_to_coll:
            p["collection"] = class_to_coll[p["collection"]]

    assert result.mongo_query_patterns[0]["collection"] == "c_word"
    assert result.mongo_query_patterns[1]["collection"] == "c_sku"
