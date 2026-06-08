"""Phase 2 P2.T13: NL paraphrases 索引升级 — 单元测试"""
import pytest
from pydantic import ValidationError

from app.knowledge.knowledge_content import build_example_content
from app.schemas.knowledge_payload import ExamplePayload, RulePayload


# ════════════════════════════════════════════
#  build_example_content
# ════════════════════════════════════════════


def test_build_example_content_with_paraphrases():
    payload = {
        "question": "昨天订单数",
        "nl_paraphrases": ["昨天有多少订单", "yesterday order count"],
    }
    result = build_example_content(payload)
    assert result == "昨天订单数\n昨天有多少订单\nyesterday order count"


def test_build_example_content_without_paraphrases():
    """向后兼容: 无 nl_paraphrases 时只返回 question"""
    payload = {
        "question": "昨天订单数",
        "target_collection": "c_product",
        "query_json": {},
    }
    result = build_example_content(payload)
    assert result == "昨天订单数"


def test_build_example_content_empty_paraphrases():
    """nl_paraphrases 为空列表时等同于无 paraphrases"""
    payload = {"question": "查询用户数", "nl_paraphrases": []}
    result = build_example_content(payload)
    assert result == "查询用户数"


def test_build_example_content_filters_empty_strings():
    """空字符串 paraphrase 被过滤"""
    payload = {"question": "查询", "nl_paraphrases": ["", "等价问法", ""]}
    result = build_example_content(payload)
    assert result == "查询\n等价问法"


def test_build_example_content_no_question():
    """question 为空时只拼 paraphrases"""
    payload = {"question": "", "nl_paraphrases": ["问法A", "问法B"]}
    result = build_example_content(payload)
    assert result == "问法A\n问法B"


# ════════════════════════════════════════════
#  ExamplePayload 新字段默认值
# ════════════════════════════════════════════


def test_example_payload_new_fields_have_defaults():
    """所有新增字段有默认值, 不破坏现有消费方"""
    p = ExamplePayload(
        question="昨天订单数",
        target_collection="c_product",
        query_json={"find": {}},
    )
    assert p.nl_paraphrases == []
    assert p.dynamic_variants == []
    assert p.extraction_source == "qmql_history"
    assert p.source_mapper is None
    assert p.source_method is None
    assert p.source_repo_id is None
    assert p.explain_verified is False


def test_example_payload_with_new_fields():
    """新字段可正常赋值"""
    p = ExamplePayload(
        question="查用户",
        target_collection="c_user",
        query_json={"find": {"status": 1}},
        nl_paraphrases=["查询用户列表", "list users"],
        dynamic_variants=[{"sql": "SELECT *", "branch_conditions": ["id非空"]}],
        extraction_source="mybatis_extract",
        source_mapper="com.example.UserMapper",
        source_method="selectAll",
        source_repo_id=42,
        explain_verified=True,
    )
    assert p.nl_paraphrases == ["查询用户列表", "list users"]
    assert p.extraction_source == "mybatis_extract"
    assert p.source_mapper == "com.example.UserMapper"
    assert p.source_method == "selectAll"
    assert p.source_repo_id == 42
    assert p.explain_verified is True


def test_example_payload_rejects_extra_fields():
    """extra=forbid 仍生效"""
    with pytest.raises(ValidationError):
        ExamplePayload(
            question="q",
            target_collection="c",
            query_json={},
            unknown_field="x",  # type: ignore[call-arg]
        )


# ════════════════════════════════════════════
#  RulePayload 新字段默认值
# ════════════════════════════════════════════


def test_rule_payload_new_fields_have_defaults():
    """RulePayload 新增字段有默认值"""
    p = RulePayload(rule_text="status=4 排除")
    assert p.rule_kind == "business_constraint"
    assert p.evidence is None


def test_rule_payload_with_new_fields():
    p = RulePayload(
        rule_text="is_deleted=0 必加",
        rule_kind="filter_default",
        evidence={"source": "mybatis", "repo_id": 1, "frequency": 95},
    )
    assert p.rule_kind == "filter_default"
    assert p.evidence == {"source": "mybatis", "repo_id": 1, "frequency": 95}


def test_rule_payload_invalid_rule_kind():
    with pytest.raises(ValidationError):
        RulePayload(rule_text="x", rule_kind="invalid_kind")  # type: ignore[arg-type]
