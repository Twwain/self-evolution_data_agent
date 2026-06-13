"""P1-14: LLM transient 错误重试测试.

覆盖:
  - _is_transient_llm_error 异常分类 (5xx/Timeout/Connection → True; ValueError → False)
  - settings.llm_retry_max / agent_loop_context_limit_tokens 配置项存在性
  - _openai_chat_with_retry 第二次成功时总调用次数 = 2
  - _openai_chat_with_retry 非 transient 错误不重试 (只调一次)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.engine.llm import _is_transient_llm_error


# ─────────────────────────────────────────────────────────────
#  1. 异常分类
# ─────────────────────────────────────────────────────────────

class TestTransientErrorClassification:
    def test_openai_timeout_is_transient(self):
        import openai
        exc = openai.APITimeoutError(request=MagicMock())
        assert _is_transient_llm_error(exc) is True

    def test_openai_connection_is_transient(self):
        import openai
        exc = openai.APIConnectionError(request=MagicMock())
        assert _is_transient_llm_error(exc) is True

    def test_openai_5xx_is_transient(self):
        import openai
        exc = openai.APIStatusError(
            message="Internal Server Error",
            response=MagicMock(status_code=500),
            body=None,
        )
        assert _is_transient_llm_error(exc) is True

    def test_openai_4xx_is_not_transient(self):
        import openai
        exc = openai.APIStatusError(
            message="Bad Request",
            response=MagicMock(status_code=400),
            body=None,
        )
        assert _is_transient_llm_error(exc) is False

    def test_value_error_is_not_transient(self):
        assert _is_transient_llm_error(ValueError("bad input")) is False

    def test_runtime_error_is_not_transient(self):
        assert _is_transient_llm_error(RuntimeError("nope")) is False


# ─────────────────────────────────────────────────────────────
#  2. Settings 存在性
# ─────────────────────────────────────────────────────────────

class TestRetryRespectsConfig:
    def test_settings_has_llm_retry_max(self):
        assert hasattr(settings, "llm_retry_max")
        assert isinstance(settings.llm_retry_max, int)
        assert settings.llm_retry_max >= 0

    def test_settings_has_agent_loop_context_limit(self):
        assert hasattr(settings, "agent_loop_context_limit_tokens")
        assert settings.agent_loop_context_limit_tokens > 0

    def test_llm_retry_max_default_is_1(self):
        # 默认 1 — 出错重试一次即可, 不要反复骚扰 LLM
        import os
        if "IS_LLM_RETRY_MAX" not in os.environ:
            assert settings.llm_retry_max == 1


# ─────────────────────────────────────────────────────────────
#  3. Retry 行为
# ─────────────────────────────────────────────────────────────

class TestRetryBehavior:
    """完整 retry 流程 — mock _openai_chat 抛 transient 错误验证重试逻辑."""

    def test_retry_succeeds_on_second_attempt(self, monkeypatch):
        import openai
        import app.engine.llm as llm_mod

        call_count = {"n": 0}

        def fake_openai_chat(messages, temp, max_tokens, extra_body=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise openai.APIConnectionError(request=MagicMock())
            return "ok"

        monkeypatch.setattr(llm_mod, "_openai_chat", fake_openai_chat)
        monkeypatch.setattr(settings, "llm_retry_max", 2)
        # 防止真实 sleep 拖慢测试
        monkeypatch.setattr(llm_mod.time, "sleep", lambda _: None)

        result = llm_mod._openai_chat_with_retry([], 0.1, 100)
        assert result == "ok"
        assert call_count["n"] == 2

    def test_no_retry_on_non_transient(self, monkeypatch):
        import app.engine.llm as llm_mod

        call_count = {"n": 0}

        def fake_openai_chat(messages, temp, max_tokens, extra_body=None):
            call_count["n"] += 1
            raise ValueError("bad input")

        monkeypatch.setattr(llm_mod, "_openai_chat", fake_openai_chat)
        monkeypatch.setattr(settings, "llm_retry_max", 3)

        with pytest.raises(ValueError):
            llm_mod._openai_chat_with_retry([], 0.1, 100)
        assert call_count["n"] == 1  # 非 transient, 立即 raise, 不重试

    def test_retry_exhausted_reraises(self, monkeypatch):
        """所有重试用完后仍抛出原始异常."""
        import openai
        import app.engine.llm as llm_mod

        call_count = {"n": 0}

        def fake_openai_chat(messages, temp, max_tokens, extra_body=None):
            call_count["n"] += 1
            raise openai.APITimeoutError(request=MagicMock())

        monkeypatch.setattr(llm_mod, "_openai_chat", fake_openai_chat)
        monkeypatch.setattr(settings, "llm_retry_max", 2)
        monkeypatch.setattr(llm_mod.time, "sleep", lambda _: None)

        with pytest.raises(openai.APITimeoutError):
            llm_mod._openai_chat_with_retry([], 0.1, 100)
        # 1 次原始调用 + 2 次重试 = 3 次
        assert call_count["n"] == 3

    def test_retry_disabled_when_max_is_zero(self, monkeypatch):
        """llm_retry_max=0 时完全禁用 retry."""
        import openai
        import app.engine.llm as llm_mod

        call_count = {"n": 0}

        def fake_openai_chat(messages, temp, max_tokens, extra_body=None):
            call_count["n"] += 1
            raise openai.APIConnectionError(request=MagicMock())

        monkeypatch.setattr(llm_mod, "_openai_chat", fake_openai_chat)
        monkeypatch.setattr(settings, "llm_retry_max", 0)

        with pytest.raises(openai.APIConnectionError):
            llm_mod._openai_chat_with_retry([], 0.1, 100)
        assert call_count["n"] == 1  # 禁用 retry, 只调一次
