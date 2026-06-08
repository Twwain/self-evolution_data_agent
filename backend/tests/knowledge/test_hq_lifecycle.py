"""Phase 3 — HQ 全生命周期测试.

覆盖:
- is_valid_covered_path 严格连续子序列校验
- generate_hq_with_validation 整合校验
- rewrite_hq_subvectors ChromaDB 写入
"""

from unittest.mock import MagicMock, patch

import pytest

from app.knowledge.hypothetical_queries import (
    HQItem,
    generate_hq_with_validation,
    is_valid_covered_path,
    text_includes_collections,
)


# ════════════════════════════════════════════
#  is_valid_covered_path
# ════════════════════════════════════════════

def test_valid_full_path():
    assert is_valid_covered_path(["a", "b", "c", "d"], ["a", "b", "c", "d"]) is True


def test_valid_prefix():
    assert is_valid_covered_path(["a", "b"], ["a", "b", "c", "d"]) is True
    assert is_valid_covered_path(["a", "b", "c"], ["a", "b", "c", "d"]) is True


def test_valid_middle():
    assert is_valid_covered_path(["b", "c"], ["a", "b", "c", "d"]) is True


def test_valid_suffix():
    assert is_valid_covered_path(["b", "c", "d"], ["a", "b", "c", "d"]) is True
    assert is_valid_covered_path(["c", "d"], ["a", "b", "c", "d"]) is True


def test_invalid_skip_middle():
    assert is_valid_covered_path(["a", "c"], ["a", "b", "c", "d"]) is False
    assert is_valid_covered_path(["a", "b", "d"], ["a", "b", "c", "d"]) is False
    assert is_valid_covered_path(["a", "d"], ["a", "b", "c", "d"]) is False


def test_invalid_reverse():
    assert is_valid_covered_path(["d", "c", "b", "a"], ["a", "b", "c", "d"]) is False
    assert is_valid_covered_path(["c", "b"], ["a", "b", "c", "d"]) is False


def test_invalid_too_long():
    assert is_valid_covered_path(["a", "b", "c", "d", "e"], ["a", "b", "c", "d"]) is False


def test_invalid_empty():
    assert is_valid_covered_path([], ["a", "b"]) is False
    assert is_valid_covered_path(["a"], []) is False


def test_invalid_not_in_route():
    assert is_valid_covered_path(["x"], ["a", "b", "c"]) is False
    assert is_valid_covered_path(["a", "x"], ["a", "b", "c"]) is False


# ════════════════════════════════════════════
#  text_includes_collections
# ════════════════════════════════════════════

def test_text_includes_strict_pass():
    assert text_includes_collections(
        "查询 orders 的数据", ["orders"], mode="strict",
    ) is True


def test_text_includes_strict_fail():
    assert text_includes_collections(
        "查询订单数据", ["orders"], mode="strict",
    ) is False


def test_text_includes_lenient_with_lookup():
    assert text_includes_collections(
        "查询订单数据", ["orders"],
        mode="lenient",
        terminology_lookup={"orders": ["orders", "订单"]},
    ) is True


def test_text_includes_off_always_true():
    assert text_includes_collections(
        "", ["orders"], mode="off",
    ) is True


# ════════════════════════════════════════════
#  generate_hq_with_validation
# ════════════════════════════════════════════

@patch("app.knowledge.hypothetical_queries._call_llm_for_hq_items")
def test_generate_hq_filters_invalid_covered_path(mock_llm):
    """LLM 产 3 条, 1 条压缩路径被过滤."""
    mock_llm.return_value = [
        HQItem(q="问题1关于a和b和c", covered_path=["a", "b", "c"]),  # ✅ 全路径
        HQItem(q="问题2关于a和c", covered_path=["a", "c"]),          # ❌ 跳 b
        HQItem(q="问题3关于b和c", covered_path=["b", "c"]),          # ✅ 中段
    ]
    valid = generate_hq_with_validation(
        content="x", entry_type="route_hint",
        route_collection_path=["a", "b", "c"],
        # text_includes_collections 默认 lenient, 无 terminology_lookup
        # 所以需要 q 文本含 collection 名
    )
    # 问题1 covered_path=[a,b,c] 合法, q 含 "a" "b" "c" → pass
    # 问题2 covered_path=[a,c] 非连续 → 被 is_valid_covered_path 拒
    # 问题3 covered_path=[b,c] 合法, q 含 "b" "c" → pass
    assert valid == ["问题1关于a和b和c", "问题3关于b和c"]


@patch("app.knowledge.hypothetical_queries._call_llm_for_hq_items")
def test_generate_hq_rule_skips_path_check(mock_llm):
    """rule 类型不做 covered_path 校验, 全部接受."""
    mock_llm.return_value = [
        HQItem(q="问题1", covered_path=["x"]),
        HQItem(q="问题2", covered_path=["y"]),
    ]
    valid = generate_hq_with_validation(
        content="x", entry_type="rule", route_collection_path=None,
    )
    assert valid == ["问题1", "问题2"]


@patch("app.knowledge.hypothetical_queries._call_llm_for_hq_items")
def test_generate_hq_route_hint_no_path_falls_through(mock_llm):
    """route_hint 但 route_collection_path 为空 → 跳过校验."""
    mock_llm.return_value = [HQItem(q="问题1", covered_path=["x"])]
    valid = generate_hq_with_validation(
        content="x", entry_type="route_hint", route_collection_path=None,
    )
    assert valid == ["问题1"]


@patch("app.knowledge.hypothetical_queries._call_llm_for_hq_items")
def test_generate_hq_non_enabled_type_returns_empty(mock_llm):
    """非 rule/route_hint 类型返空."""
    valid = generate_hq_with_validation(
        content="x", entry_type="terminology", route_collection_path=None,
    )
    assert valid == []
    mock_llm.assert_not_called()


# ════════════════════════════════════════════
#  rewrite_hq_subvectors
# ════════════════════════════════════════════

def test_rewrite_hq_subvectors_deletes_old_then_upserts_new():
    """rewrite_hq_subvectors 先 delete entry_id 全部条目, 再 upsert 主+hq."""
    from app.knowledge.knowledge_retriever import rewrite_hq_subvectors

    fake_coll = MagicMock()
    with patch(
        "app.engine.registry.get_knowledge_collection",
        return_value=fake_coll,
    ):
        rewrite_hq_subvectors(
            slug="test_ns", entry_id=42, entry_type="route_hint",
            tier="normal", namespace_id=1,
            content="主向量内容", hq_list=["问题1", "问题2", "问题3"],
        )

    fake_coll.delete.assert_called_once_with(where={"entry_id": 42})
    fake_coll.upsert.assert_called_once()
    upsert_args = fake_coll.upsert.call_args
    ids = upsert_args.kwargs["ids"]
    assert len(ids) == 4  # 主 + 3 hq
    assert ids[0] == "ke_42"
    assert ids[1] == "ke_42_hq_0"
    assert ids[2] == "ke_42_hq_1"
    assert ids[3] == "ke_42_hq_2"

    documents = upsert_args.kwargs["documents"]
    assert documents == ["主向量内容", "问题1", "问题2", "问题3"]


def test_rewrite_hq_subvectors_empty_hq_list():
    """hq_list 为空时只写主向量."""
    from app.knowledge.knowledge_retriever import rewrite_hq_subvectors

    fake_coll = MagicMock()
    with patch(
        "app.engine.registry.get_knowledge_collection",
        return_value=fake_coll,
    ):
        rewrite_hq_subvectors(
            slug="test_ns", entry_id=10, entry_type="rule",
            tier="normal", namespace_id=1,
            content="内容", hq_list=[],
        )

    upsert_args = fake_coll.upsert.call_args
    ids = upsert_args.kwargs["ids"]
    assert len(ids) == 1
    assert ids[0] == "ke_10"
