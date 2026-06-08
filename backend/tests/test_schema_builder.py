"""
schema_builder 单元测试 — 覆盖全部公开函数

现有函数: build_ddl_from_jpa, build_doc_from_jpa, build_example_sql_from_mybatis
"""

import pytest

from app.knowledge.schema_builder import (
    build_ddl_from_jpa,
    build_doc_from_jpa,
    build_example_sql_from_mybatis,
)


# ════════════════════════════════════════════
#  build_ddl_from_jpa
# ════════════════════════════════════════════

def test_build_ddl_from_jpa():
    entities = [{
        "class_name": "User",
        "table": "users",
        "columns": [
            {"name": "id", "type": "Long", "column": "id"},
            {"name": "name", "type": "String", "column": "name"},
        ],
    }]
    result = build_ddl_from_jpa(entities)
    assert len(result) == 1
    assert result[0] == "CREATE TABLE users (id BIGINT, name VARCHAR(255));"


def test_build_ddl_from_jpa_empty():
    assert build_ddl_from_jpa([]) == []


# ════════════════════════════════════════════
#  build_doc_from_jpa
# ════════════════════════════════════════════

def test_build_doc_from_jpa_with_relations():
    entities = [{
        "class_name": "User",
        "table": "users",
        "columns": [
            {"name": "id", "type": "Long", "column": "id"},
        ],
        "relations": [
            {"type": "ManyToOne", "target": "Department", "field": "dept"},
        ],
    }]
    result = build_doc_from_jpa(entities)
    assert len(result) == 1
    assert "关联关系: dept→Department(ManyToOne)" in result[0]


# ════════════════════════════════════════════
#  build_example_sql_from_mybatis
# ════════════════════════════════════════════

def test_build_example_sql_only_select():
    entries = [
        {"id": "insert", "type": "insert", "sql": "INSERT INTO t VALUES(#{id})"},
        {"id": "sel", "type": "select", "sql": "SELECT * FROM t WHERE id = #{id}"},
    ]
    result = build_example_sql_from_mybatis(entries)
    # insert 被过滤, #{id} 替换为 :id
    assert len(result) == 1
    assert ":id" in result[0]
    assert "#{" not in result[0]
