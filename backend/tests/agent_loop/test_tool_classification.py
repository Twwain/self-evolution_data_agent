"""分类常量完备性 + 互斥性 AST 守卫."""
from app.engine.tools.classification import (
    EXPLORATORY, DECISIVE, INTERACTIVE, classify_tool,
)
from app.engine.tools.registry import REGISTRY


def test_classification_covers_all_10_tools():
    """10 tool 必须全部归类, 缺一个立即失败 (新增 tool 必须显式分类)."""
    classified = EXPLORATORY | DECISIVE | INTERACTIVE
    assert classified == set(REGISTRY.keys()), (
        f"未分类: {set(REGISTRY.keys()) - classified}; "
        f"幽灵: {classified - set(REGISTRY.keys())}"
    )


def test_categories_are_disjoint():
    """同一 tool 不能同属两类."""
    assert EXPLORATORY & DECISIVE == set()
    assert EXPLORATORY & INTERACTIVE == set()
    assert DECISIVE & INTERACTIVE == set()


def test_classify_tool_returns_category():
    assert classify_tool("lookup_knowledge") == "exploratory"
    assert classify_tool("execute_plan") == "decisive"
    assert classify_tool("clarify_with_user") == "interactive"
    assert classify_tool("fetch_schema") == "exploratory"
    assert classify_tool("execute_query") == "decisive"


def test_classify_unknown_tool_raises():
    import pytest
    with pytest.raises(KeyError, match="unknown_tool"):
        classify_tool("unknown_tool")


def test_expected_counts():
    """锁定数量, 防意外漂移."""
    assert len(EXPLORATORY) == 5
    assert len(DECISIVE) == 4
    assert len(INTERACTIVE) == 1
