"""
P1-22 D9: intake LLM 失败时 split/conflict 必须 raise IntakeLLMError。

设计原则:
- propose_split 失败 → raise (失败与"不可分"语义无法区分, 静默 fallback 掩盖故障)
- detect_conflicts 失败 → raise (LLM 不可用时静默放过 → 录脏知识, 危险)
- refine_knowledge 失败 → 保持 fallback, 不 raise (raw 原文本身有效降级)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.knowledge.intake import (
    IntakeLLMError,
    detect_conflicts,
    propose_split,
    refine_knowledge,
)


# ─────────────────────────── propose_split ───────────────────────────

class TestProposeSplitRaises:
    def test_raises_on_network_error(self):
        """LLM 网络异常 → IntakeLLMError (而非静默返空列表)."""
        with patch("app.knowledge.intake.chat_completion") as mock_llm:
            mock_llm.side_effect = RuntimeError("connection refused")
            with pytest.raises(IntakeLLMError, match="split LLM 不可用"):
                propose_split("超过 200 字的超长强约束条目" * 20)

    def test_raises_on_timeout(self):
        """LLM 超时 → IntakeLLMError."""
        with patch("app.knowledge.intake.chat_completion") as mock_llm:
            mock_llm.side_effect = TimeoutError("upstream timeout")
            with pytest.raises(IntakeLLMError) as exc_info:
                propose_split("some very long content that needs splitting")
            # 原始异常链完整 (from e)
            assert exc_info.value.__cause__ is not None

    def test_split_returns_list_on_success(self):
        """LLM 正常返回 → 返回列表 (成功路径不受影响)."""
        fake_response = '{"candidates":[{"refined":"规则A","description":"说明A"}]}'
        with patch("app.knowledge.intake.chat_completion", return_value=fake_response):
            result = propose_split("内容")
        assert len(result) == 1
        assert result[0].refined == "规则A"


# ─────────────────────────── detect_conflicts ───────────────────────────

class TestDetectConflictsRaises:
    def test_raises_on_llm_failure(self):
        """LLM 故障 → IntakeLLMError (而非静默返空冲突报告)."""
        with patch("app.knowledge.intake.chat_completion") as mock_llm:
            mock_llm.side_effect = RuntimeError("timeout")
            with pytest.raises(IntakeLLMError, match="conflict 检测 LLM 不可用"):
                detect_conflicts(
                    new_content="新知识条目",
                    existing=[{"id": 1, "content": "既有条目内容"}],
                )

    def test_raises_preserves_cause(self):
        """raise 链完整 — 调试时可溯源 LLM 原始异常."""
        original = ConnectionError("dns lookup failed")
        with patch("app.knowledge.intake.chat_completion") as mock_llm:
            mock_llm.side_effect = original
            with pytest.raises(IntakeLLMError) as exc_info:
                detect_conflicts("content", [{"id": 1, "content": "x"}])
            assert exc_info.value.__cause__ is original

    def test_empty_existing_skips_llm(self):
        """existing=[] 时不调 LLM → 直接返空报告 (快路径不受影响)."""
        with patch("app.knowledge.intake.chat_completion") as mock_llm:
            mock_llm.side_effect = RuntimeError("should not be called")
            report = detect_conflicts("content", [])
        assert report.items == []
        mock_llm.assert_not_called()


# ─────────────────────────── refine_knowledge ───────────────────────────

class TestRefineKnowledgeFallback:
    def test_does_not_raise_uses_raw(self):
        """refine 失败保持 fallback 不 raise — raw 原文是有效降级 (P1-22 有意识决策)."""
        with patch("app.knowledge.intake.chat_completion") as mock_llm:
            mock_llm.side_effect = RuntimeError("timeout")
            result = refine_knowledge("rule", "原始内容文本", tier="normal")
        # fallback: refined = raw 原文
        assert result.refined == "原始内容文本"
        # description 为空 (无 LLM 产出)
        assert result.description == ""

    def test_fallback_overflow_flag_correct_for_critical(self):
        """refine 失败 fallback 时 overflow 标志按 raw 长度计算 (critical tier)."""
        long_raw = "x" * 300  # > CRITICAL_MAX_CHARS=200
        with patch("app.knowledge.intake.chat_completion") as mock_llm:
            mock_llm.side_effect = RuntimeError("fail")
            result = refine_knowledge("rule", long_raw, tier="critical")
        assert result.overflow is True
        assert result.refined == long_raw
