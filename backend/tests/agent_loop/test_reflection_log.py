"""Stage 2 抓手 C — reflection 抽取 (结构化锚点解析)."""

from app.engine.agent_loop import _extract_reflection


def test_extract_full_reflection():
    text = """让我看看锚点是否覆盖.
[REFLECTION]
confidence: 0.8
reason: 锚点未覆盖 VIP, 调 lookup_knowledge
alternative: 直接 fetch_schema 但术语不明会浪费查询
[/REFLECTION]
然后我开始调 tool.
"""
    r = _extract_reflection(text, "lookup_knowledge")
    assert r is not None
    assert r["tool_name"] == "lookup_knowledge"
    assert r["confidence"] == 0.8
    assert "锚点" in r["reason"]
    assert "fetch_schema" in r["alternative"]


def test_extract_no_anchors_returns_none():
    """没有 [REFLECTION] 锚点 → 跳过, 返 None."""
    assert _extract_reflection("just thinking, no anchors", "fetch_schema") is None


def test_extract_partial_only_reason():
    text = """[REFLECTION]
reason: 测试一下
[/REFLECTION]"""
    r = _extract_reflection(text, "fetch_schema")
    assert r is not None
    assert r["reason"] == "测试一下"
    assert r["confidence"] is None
    assert r["alternative"] == ""


def test_extract_invalid_confidence_falls_back_to_none():
    """confidence 非数 / 越界 → confidence=None, 其他字段仍提取."""
    text = """[REFLECTION]
confidence: NaN
reason: x
[/REFLECTION]"""
    r = _extract_reflection(text, "tool")
    assert r is not None
    assert r["confidence"] is None  # NaN 走 0.0-1.0 范围检查 fail
    assert r["reason"] == "x"


def test_extract_out_of_range_confidence_dropped():
    text = """[REFLECTION]
confidence: 1.5
reason: x
[/REFLECTION]"""
    r = _extract_reflection(text, "tool")
    assert r is not None
    assert r["confidence"] is None


def test_extract_empty_text_returns_none():
    """空字符串 → None."""
    assert _extract_reflection("", "tool") is None
