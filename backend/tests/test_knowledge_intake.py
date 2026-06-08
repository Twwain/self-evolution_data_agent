from unittest.mock import patch

import pytest

from app.knowledge.intake import refine_knowledge, propose_split, CRITICAL_MAX_CHARS
from app.knowledge.intake import detect_conflicts, ConflictReport


def _mock_llm(json_body: str):
    return patch("app.knowledge.intake.chat_completion", return_value=json_body)


def test_refine_produces_refined_and_description():
    resp = '{"refined":"GMV = 含退款的总成交额(单位:分)","description":"GMV 口径"}'
    with _mock_llm(resp):
        r = refine_knowledge("terminology", "GMV 就是成交额那个,退款也算在里面", tier="normal")
    assert r.refined.startswith("GMV")
    assert r.description == "GMV 口径"
    assert r.overflow is False


def test_refine_overflow_flag_when_critical_exceeds_limit():
    long_text = "x" * (CRITICAL_MAX_CHARS + 50)
    resp = f'{{"refined":"{long_text}","description":"超长"}}'
    with _mock_llm(resp):
        r = refine_knowledge("terminology", "一大段文字...", tier="critical")
    assert r.overflow is True
    assert len(r.refined) > CRITICAL_MAX_CHARS


def test_refine_llm_failure_falls_back_to_raw():
    with patch("app.knowledge.intake.chat_completion", side_effect=RuntimeError("LLM down")):
        r = refine_knowledge("terminology", "原始内容", tier="normal")
    assert r.refined == "原始内容"
    assert r.description == ""
    assert r.overflow is False


def test_refine_no_json_in_response_falls_back_to_raw():
    with _mock_llm("抱歉，我无法处理这个请求"):
        r = refine_knowledge("terminology", "原始内容", tier="normal")
    assert r.refined == "原始内容"
    assert r.description == ""
    assert r.overflow is False


def test_refine_malformed_json_falls_back_to_raw():
    with _mock_llm("{invalid json}"):
        r = refine_knowledge("terminology", "原始内容", tier="normal")
    assert r.refined == "原始内容"
    assert r.description == ""


def test_refine_critical_long_raw_overflow_in_fallback():
    """LLM 故障路径下,长 raw + critical 仍要标 overflow,防止绕过 split"""
    long_raw = "x" * (CRITICAL_MAX_CHARS + 50)
    with patch("app.knowledge.intake.chat_completion", side_effect=RuntimeError("LLM down")):
        r = refine_knowledge("terminology", long_raw, tier="critical")
    assert r.refined == long_raw
    assert r.overflow is True


# ─────────────────────────── propose_split 测试 ───────────────────────────
def test_propose_split_returns_multiple_rules():
    resp = ('{"candidates":[{"refined":"订单状态=1 表示已支付","description":"支付口径"},'
            '{"refined":"订单状态=2 表示已退款","description":"退款口径"}]}')
    with _mock_llm(resp):
        cands = propose_split("订单状态 1 已支付 2 已退款 3 已关闭 ...(超长)")
    assert len(cands) == 2
    assert cands[0].refined.startswith("订单状态=1")
    assert cands[0].description == "支付口径"
    assert cands[0].overflow is False


def test_propose_split_llm_failure_returns_empty():
    from app.knowledge.intake import IntakeLLMError
    with patch("app.knowledge.intake.chat_completion", side_effect=RuntimeError("LLM down")):
        with pytest.raises((RuntimeError, IntakeLLMError)):
            propose_split("一大段内容")


def test_propose_split_malformed_json_returns_empty():
    with _mock_llm("not a valid JSON response"):
        cands = propose_split("一大段内容")
    assert cands == []


def test_propose_split_filters_empty_refined():
    """候选 refined 为空的条目应被丢弃"""
    resp = '{"candidates":[{"refined":"","description":"空"},{"refined":"有效","description":"D"}]}'
    with _mock_llm(resp):
        cands = propose_split("...")
    assert len(cands) == 1
    assert cands[0].refined == "有效"


# ─────────────────────────── detect_conflicts 测试 ───────────────────────────
def test_detect_conflicts_flags_contradictory():
    existing = [
        {"id": 1, "content": "有效订单指 status=1"},
        {"id": 2, "content": "有效订单指 status IN (1,2)"},
    ]
    resp = ('{"conflicts":[{"existing_id":1,"reason":"status 取值不一致",'
            '"suggested":"merge"}]}')
    with _mock_llm(resp):
        report = detect_conflicts("有效订单指 status=2", existing)
    assert isinstance(report, ConflictReport)
    assert len(report.items) == 1
    assert report.items[0].existing_id == 1
    assert report.items[0].suggested == "merge"


def test_detect_conflicts_empty_when_no_hits():
    with _mock_llm('{"conflicts":[]}'):
        report = detect_conflicts("无关新条目", [{"id": 9, "content": "完全不同"}])
    assert report.items == []


def test_detect_conflicts_skips_when_no_existing():
    with patch("app.knowledge.intake.chat_completion") as m:
        report = detect_conflicts("新条目", [])
    assert report.items == []
    m.assert_not_called()


def test_detect_conflicts_llm_failure_returns_empty():
    from app.knowledge.intake import IntakeLLMError
    with patch("app.knowledge.intake.chat_completion", side_effect=RuntimeError("LLM down")):
        with pytest.raises(IntakeLLMError):
            detect_conflicts("新", [{"id": 1, "content": "旧"}])


def test_detect_conflicts_invalid_item_skipped():
    """LLM 返回缺字段或类型错的 item, 跳过而非抛错"""
    resp = ('{"conflicts":['
            '{"existing_id":"not-an-int","reason":"x","suggested":"merge"},'
            '{"existing_id":2,"reason":"ok","suggested":"replace"}'
            ']}')
    with _mock_llm(resp):
        report = detect_conflicts("新", [{"id": 2, "content": "旧"}])
    assert len(report.items) == 1
    assert report.items[0].existing_id == 2


def test_detect_conflicts_invalid_suggested_falls_back_to_coexist():
    """LLM 返回非枚举值的 suggested, 静默回退 coexist"""
    resp = '{"conflicts":[{"existing_id":1,"reason":"x","suggested":"replace_all"}]}'
    with _mock_llm(resp):
        report = detect_conflicts("新", [{"id": 1, "content": "旧"}])
    assert len(report.items) == 1
    assert report.items[0].suggested == "coexist"
