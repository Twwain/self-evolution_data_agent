"""10 agent tool 静态分类. agent_loop 用此区分配额桶.

设计: 集合互斥 + REGISTRY 完备性由 test_tool_classification.py AST 守卫,
新增 tool 必须显式入桶, 否则 CI 红.
"""
from __future__ import annotations

EXPLORATORY: frozenset[str] = frozenset({
    "lookup_knowledge",
    "save_knowledge",
    "fetch_schema",
    "inspect_values",
    "estimate_cost",
})
"""探索类 — read-only / 学习副作用 / schema 探查, 不直接推进最终答案."""

DECISIVE: frozenset[str] = frozenset({
    "execute_query",
    "generate_query_plan",
    "execute_plan",
    "present_result",
})
"""决策类 — 直接产出最终结果或图表选型."""

INTERACTIVE: frozenset[str] = frozenset({
    "clarify_with_user",
})
"""交互类 — 等用户输入, 不计任何配额."""


def classify_tool(name: str) -> str:
    """返 'exploratory' | 'decisive' | 'interactive', unknown 抛 KeyError."""
    if name in EXPLORATORY:
        return "exploratory"
    if name in DECISIVE:
        return "decisive"
    if name in INTERACTIVE:
        return "interactive"
    raise KeyError(f"unknown tool name: {name!r}")
