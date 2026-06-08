"""Tests for extraction_prompts.py — prompt templates + with_retry wrapper.

Covers:
  - ENUM_CLASS_EXTRACTION_PROMPT produces valid JSON when LLM responds correctly
  - with_retry succeeds after transient failure
  - with_retry gives up after max_attempts
  - All prompts pass product safety checklist (no banned words)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from app.knowledge.extraction_prompts import (
    BUSINESS_RULE_EXTRACTION_PROMPT,
    ENUM_CLASS_EXTRACTION_PROMPT,
    ExtractionLLMError,
    RELATIONSHIP_DAO_EXTRACTION_PROMPT,
    TERMINOLOGY_EXTRACTION_PROMPT,
    _is_retryable,
    llm_extract_with_retry,
)


# ─────────────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────────────

MOCK_ENUM_RESPONSE = json.dumps({
    "enum_class": "OrderStatus",
    "fully_qualified_name": "com.example.enums.OrderStatus",
    "values": [
        {"name": "CREATED", "db_value": 1, "description": "已创建"},
        {"name": "PAID", "db_value": 2, "description": "已支付"},
    ],
})


# ─────────────────────────────────────────────────────────────
#  1. Enum prompt produces valid JSON
# ─────────────────────────────────────────────────────────────

class TestEnumPrompt:
    def test_enum_prompt_produces_valid_json(self):
        """Mock LLM with realistic response, verify JSON structure."""
        with patch("app.knowledge.extraction_prompts.asyncio.to_thread") as mock_thread:
            mock_thread.return_value = MOCK_ENUM_RESPONSE

            result = asyncio.run(
                llm_extract_with_retry(
                    ENUM_CLASS_EXTRACTION_PROMPT.format(
                        enum_source="public enum OrderStatus { CREATED(1), PAID(2); }"
                    ),
                    template_name="enum_class_extraction",
                    max_attempts=1,
                )
            )

        data = json.loads(result)
        assert "enum_class" in data
        assert "fully_qualified_name" in data
        assert "values" in data
        assert isinstance(data["values"], list)
        assert len(data["values"]) == 2
        for v in data["values"]:
            assert "name" in v
            assert "db_value" in v

    def test_enum_prompt_has_placeholders(self):
        """Verify the prompt uses {enum_source} placeholder."""
        assert "{enum_source}" in ENUM_CLASS_EXTRACTION_PROMPT


# ─────────────────────────────────────────────────────────────
#  3. with_retry succeeds after transient failure
# ─────────────────────────────────────────────────────────────

class TestWithRetrySuccess:
    def test_succeeds_after_transient_failure(self):
        """First call raises retryable error, second succeeds."""
        import openai

        call_count = {"n": 0}

        async def fake_to_thread(fn, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise openai.APIConnectionError(request=MagicMock())
            return '{"result": "ok"}'

        with patch("app.knowledge.extraction_prompts.asyncio.to_thread", side_effect=fake_to_thread):
            with patch("app.knowledge.extraction_prompts.asyncio.sleep", return_value=None):
                result = asyncio.run(
                    llm_extract_with_retry(
                        "test prompt",
                        template_name="test",
                        max_attempts=3,
                        base_delay=0.01,
                    )
                )

        assert result == '{"result": "ok"}'
        assert call_count["n"] == 2


# ─────────────────────────────────────────────────────────────
#  4. with_retry gives up after max_attempts
# ─────────────────────────────────────────────────────────────

class TestWithRetryExhausted:
    def test_gives_up_after_max_attempts(self):
        """All attempts fail → raises ExtractionLLMError with history."""
        import openai

        call_count = {"n": 0}

        async def fake_to_thread(fn, **kwargs):
            call_count["n"] += 1
            raise openai.APITimeoutError(request=MagicMock())

        with patch("app.knowledge.extraction_prompts.asyncio.to_thread", side_effect=fake_to_thread):
            with patch("app.knowledge.extraction_prompts.asyncio.sleep", return_value=None):
                with pytest.raises(ExtractionLLMError) as exc_info:
                    asyncio.run(
                        llm_extract_with_retry(
                            "test prompt",
                            template_name="test_exhausted",
                            max_attempts=3,
                            base_delay=0.01,
                        )
                    )

        err = exc_info.value
        assert err.template_name == "test_exhausted"
        assert len(err.attempts) == 3
        assert call_count["n"] == 3

    def test_non_retryable_error_raises_immediately(self):
        """Non-retryable error (4xx) raises ExtractionLLMError on first attempt."""
        import openai

        call_count = {"n": 0}

        async def fake_to_thread(fn, **kwargs):
            call_count["n"] += 1
            raise openai.BadRequestError(
                message="Bad Request",
                response=MagicMock(status_code=400),
                body=None,
            )

        with patch("app.knowledge.extraction_prompts.asyncio.to_thread", side_effect=fake_to_thread):
            with pytest.raises(ExtractionLLMError) as exc_info:
                asyncio.run(
                    llm_extract_with_retry(
                        "test prompt",
                        template_name="test_no_retry",
                        max_attempts=4,
                        base_delay=0.01,
                    )
                )

        err = exc_info.value
        assert len(err.attempts) == 1
        assert err.attempts[0]["action"] == "give_up"
        assert call_count["n"] == 1


# ─────────────────────────────────────────────────────────────
#  5. Product safety checklist — no banned words
# ─────────────────────────────────────────────────────────────

# Banned patterns: customer-specific collection/table names, domain words
# that leak customer context into prompt templates.
# NOTE: these are intentionally customer-style examples (NOT generic e-commerce
# words like 订单/product) — the guard asserts such customer vocabulary never
# leaks into prompt templates, so they must stay distinct from the words
# test_uses_generic_ecommerce_domain expects to be present.
_BANNED_PATTERNS = [
    # Customer-specific collection names (examples of what should NOT appear)
    "c_transcript", "c_exam", "c_quiz_bank",
    # Customer-specific domain words
    "成绩单", "考试", "题库", "阅卷",
]

ALL_PROMPTS = {
    "ENUM_CLASS_EXTRACTION_PROMPT": ENUM_CLASS_EXTRACTION_PROMPT,
    "TERMINOLOGY_EXTRACTION_PROMPT": TERMINOLOGY_EXTRACTION_PROMPT,
    "BUSINESS_RULE_EXTRACTION_PROMPT": BUSINESS_RULE_EXTRACTION_PROMPT,
    "RELATIONSHIP_DAO_EXTRACTION_PROMPT": RELATIONSHIP_DAO_EXTRACTION_PROMPT,
}


class TestProductSafetyChecklist:
    @pytest.mark.parametrize("prompt_name,prompt_text", ALL_PROMPTS.items())
    def test_no_banned_words(self, prompt_name: str, prompt_text: str):
        """Each prompt must not contain customer-specific vocabulary."""
        for banned in _BANNED_PATTERNS:
            assert banned not in prompt_text, (
                f"{prompt_name} contains banned word: '{banned}'"
            )

    @pytest.mark.parametrize("prompt_name,prompt_text", ALL_PROMPTS.items())
    def test_uses_generic_ecommerce_domain(self, prompt_name: str, prompt_text: str):
        """Each prompt should reference generic e-commerce concepts."""
        # At least one generic domain word should appear in examples
        generic_words = ["订单", "order", "用户", "user", "商品", "product", "SKU", "sku"]
        has_generic = any(w in prompt_text for w in generic_words)
        assert has_generic, (
            f"{prompt_name} does not use generic e-commerce domain in examples"
        )

    @pytest.mark.parametrize("prompt_name,prompt_text", ALL_PROMPTS.items())
    def test_has_output_format(self, prompt_name: str, prompt_text: str):
        """Each prompt must specify output format (D2 compliance)."""
        assert "Output format" in prompt_text or "输出" in prompt_text, (
            f"{prompt_name} missing output format specification"
        )

    @pytest.mark.parametrize("prompt_name,prompt_text", ALL_PROMPTS.items())
    def test_has_role(self, prompt_name: str, prompt_text: str):
        """Each prompt must specify a role (D2 compliance)."""
        assert "Role:" in prompt_text, (
            f"{prompt_name} missing Role specification"
        )


# ─────────────────────────────────────────────────────────────
#  6. _is_retryable classification
# ─────────────────────────────────────────────────────────────

class TestRelationshipPrompt:
    def test_relationship_prompt_has_placeholders(self):
        """Verify the prompt uses {dao_source} and {entity_schema_context} placeholders."""
        assert "{dao_source}" in RELATIONSHIP_DAO_EXTRACTION_PROMPT
        assert "{entity_schema_context}" in RELATIONSHIP_DAO_EXTRACTION_PROMPT


# ─────────────────────────────────────────────────────────────
#  7. _is_retryable classification
# ─────────────────────────────────────────────────────────────

class TestIsRetryable:
    def test_timeout_is_retryable(self):
        assert _is_retryable(asyncio.TimeoutError()) is True

    def test_value_error_is_not_retryable(self):
        assert _is_retryable(ValueError("bad")) is False

    def test_openai_429_is_retryable(self):
        import openai
        exc = openai.RateLimitError(
            message="Rate limit",
            response=MagicMock(status_code=429),
            body=None,
        )
        assert _is_retryable(exc) is True

    def test_openai_400_is_not_retryable(self):
        import openai
        exc = openai.BadRequestError(
            message="Bad Request",
            response=MagicMock(status_code=400),
            body=None,
        )
        assert _is_retryable(exc) is False
