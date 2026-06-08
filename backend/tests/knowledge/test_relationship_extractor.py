"""Phase 2 Task 5: 5 类 relationship 信号抽取与聚合."""
import pytest

from app.knowledge.relationship_extractor import (
    aggregate_relationships,
    extract_dao_relationships,
)


def test_mysql_fk_passes_through():
    """显式 1: MySQL FK 直接透传."""
    mysql_fks = [
        {
            "from_db_type": "mysql",
            "from_database": "test_db",
            "from_target": "t_order",
            "from_field": "user_id",
            "to_db_type": "mysql",
            "to_database": "test_db",
            "to_target": "t_user",
            "to_field": "id",
            "relation_type": "many_to_one",
            "is_required": True,
            "evidence": [{"source": "introspect_fk", "constraint": "fk_order_user"}],
        }
    ]
    rels = aggregate_relationships(
        mysql_fks=mysql_fks, jpa_entities=[], mongo_documents=[],
        mybatis_joins=[], dao_relations=[],
    )
    assert len(rels) == 1
    assert rels[0]["from_target"] == "t_order"
    assert rels[0]["to_target"] == "t_user"
    assert rels[0]["evidence"][0]["source"] == "introspect_fk"


def test_jpa_relations_extracted():
    """显式 2: JPA relations 从 entity 透传."""
    jpa_entities = [
        {
            "table": "t_order",
            "relations": [
                {"kind": "many_to_one", "target": "t_user", "field": "user_id",
                 "to_field": "id", "javadoc": "/** 关联用户 */"}
            ],
        }
    ]
    rels = aggregate_relationships(
        mysql_fks=[], jpa_entities=jpa_entities, mongo_documents=[],
        mybatis_joins=[], dao_relations=[],
    )
    assert len(rels) == 1
    assert rels[0]["from_target"] == "t_order"
    assert rels[0]["evidence"][0]["source"] == "code_jpa_relation"


def test_mongo_dbref_collected():
    """显式 3: Mongo @DBRef 字段."""
    mongo_documents = [
        {
            "collection": "audit_logs",
            "fields": [
                {"name": "user", "type": "DBRef", "ref_target": "users",
                 "ref_kind": "code_dbref"}
            ],
        }
    ]
    rels = aggregate_relationships(
        mysql_fks=[], jpa_entities=[], mongo_documents=mongo_documents,
        mybatis_joins=[], dao_relations=[],
    )
    assert len(rels) == 1
    assert rels[0]["to_target"] == "users"
    assert rels[0]["evidence"][0]["source"] == "code_dbref"


def test_mybatis_join_below_threshold_filtered(monkeypatch):
    """弱 1: JOIN 命中 < threshold 时不入候选."""
    monkeypatch.setattr("app.config.settings.relationship_join_hit_threshold", 5)
    monkeypatch.setattr("app.config.settings.relationship_join_mapper_threshold", 2)
    mybatis_joins = [
        {"from_target": "t_order", "from_field": "user_id",
         "to_target": "t_user", "to_field": "id",
         "mapper": "OrderMapper", "hit_count": 1},
    ]
    rels = aggregate_relationships(
        mysql_fks=[], jpa_entities=[], mongo_documents=[],
        mybatis_joins=mybatis_joins, dao_relations=[],
    )
    assert rels == []


def test_mybatis_join_above_threshold_kept(monkeypatch):
    """弱 1: JOIN 命中 >= threshold 且 mapper >= threshold 时入候选."""
    monkeypatch.setattr("app.config.settings.relationship_join_hit_threshold", 5)
    monkeypatch.setattr("app.config.settings.relationship_join_mapper_threshold", 2)
    mybatis_joins = [
        {"from_target": "t_order", "from_field": "user_id",
         "to_target": "t_user", "to_field": "id",
         "mapper": "OrderMapper", "hit_count": 4},
        {"from_target": "t_order", "from_field": "user_id",
         "to_target": "t_user", "to_field": "id",
         "mapper": "ReportMapper", "hit_count": 3},
    ]
    rels = aggregate_relationships(
        mysql_fks=[], jpa_entities=[], mongo_documents=[],
        mybatis_joins=mybatis_joins, dao_relations=[],
    )
    assert len(rels) == 1
    assert rels[0]["evidence"][0]["source"] == "usage_join_pattern"
    assert rels[0]["evidence"][0]["hit_count"] == 7


@pytest.mark.asyncio
async def test_dao_two_step_query_detected(fake_llm, tmp_path):
    """弱 2: DAO findById + findById 链路 → LLM 抽取."""
    fake_llm.queue_response({
        "relationships": [
            {
                "kind": "two_step_query",
                "from_target": "audit_logs",
                "from_field": "userId",
                "to_target": "users",
                "to_field": "_id",
                "evidence": {"method": "AuditService.findByUser",
                             "pattern": "two_step_query",
                             "snippet": "auditDao.findById(...).then(usersDao.findById(...))"},
            }
        ]
    })
    # Create a dummy DAO file
    dao_file = tmp_path / "AuditService.java"
    dao_file.write_text("public class AuditService { }")

    rels = await extract_dao_relationships(
        dao_files=[str(dao_file)],
        entity_summary={"AuditLog": ["userId"], "User": ["_id"]},
    )
    assert len(rels) == 1
    assert rels[0]["evidence"]["pattern"] == "two_step_query"
