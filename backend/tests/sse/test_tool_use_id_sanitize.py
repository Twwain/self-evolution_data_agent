"""
Bedrock proxy tool_use_id 合规性测试
=====================================
Bedrock proxy 对 tool_use_id 强制 ^[a-zA-Z0-9_-]+$ 校验;
_sanitize_tool_use_id 负责在 ID 离开 Claude provider 路径前完成清洗.
"""
from app.engine.llm import _sanitize_tool_use_id


def test_clean_id_unchanged():
    """合规 ID 原样穿透，不引入任何变化"""
    assert _sanitize_tool_use_id("toolu_01abc-DEF_123") == "toolu_01abc-DEF_123"


def test_dot_replaced():
    """点号 (.) 不合规，替换为下划线"""
    assert _sanitize_tool_use_id("toolu.01") == "toolu_01"


def test_colon_replaced():
    """冒号 (:) 不合规，每个冒号均替换为下划线"""
    assert _sanitize_tool_use_id("tool:id:123") == "tool_id_123"


def test_empty_returns_fallback():
    """空字符串返回兜底 ID，防止 Bedrock 侧 400"""
    assert _sanitize_tool_use_id("") == "tool_id_unknown"


def test_all_unsafe_chars_replaced():
    """全点号字符串应全部替换为下划线"""
    assert _sanitize_tool_use_id("...") == "___"
