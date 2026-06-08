"""T2 — semantic_llm strategy 单测 (新增能力).

4 case:
1. LLM 返回 equivalent=true, confidence >= 0.7 → 返 (winner, reason)
2. LLM 返回 equivalent=false → 返 None
3. LLM provider error (不抛) → 返 None
4. token 预算用尽 → 直接返 None, 不发请求
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.knowledge.equivalence.strategies.semantic_llm import (
    semantic_llm_checker,
    _SemanticBudget,
)


def _make_candidate(value: str, cand_id: int = 1):
    """构造最小 SchemaCanonicalCandidate mock."""

    class FakeCandidate:
        def __init__(self, val, id_):
            self.id = id_
            self.candidate_value_json = json.dumps({"description": val}, ensure_ascii=False)
            self.db_type = "mysql"
            self.target = "orders"
            self.field_path = "status"
            self.candidate_kind = "field_description"

    return FakeCandidate(value, cand_id)


@pytest.fixture(autouse=True)
def _reset_budget():
    """每个测试重置预算计数器."""
    _SemanticBudget.reset()
    yield
    _SemanticBudget.reset()


class TestSemanticLLMEquivalent:
    @pytest.mark.asyncio
    async def test_llm_returns_equivalent_true_high_confidence(self):
        """LLM 判等价 + confidence >= 0.7 → 返 (winner, reason)."""
        llm_response = json.dumps({
            "equivalent": True,
            "confidence": 0.92,
            "reason": "措辞差异, 语义同",
        })

        c1 = _make_candidate("订单当前状态", cand_id=1)
        c2 = _make_candidate("订单状态", cand_id=2)

        with patch(
            "app.knowledge.equivalence.strategies.semantic_llm._call_llm",
            new_callable=AsyncMock,
            return_value=llm_response,
        ):
            result = await semantic_llm_checker([c1, c2])

        assert result is not None
        winner, reason = result
        assert winner.id in (1, 2)
        assert "matched" in reason


class TestSemanticLLMNotEquivalent:
    @pytest.mark.asyncio
    async def test_llm_returns_equivalent_false(self):
        """LLM 判不等价 → 返 None."""
        llm_response = json.dumps({
            "equivalent": False,
            "confidence": 0.85,
            "reason": "A 含单位信息, B 缺",
        })

        c1 = _make_candidate("商品零售价, 单位元", cand_id=1)
        c2 = _make_candidate("商品价格", cand_id=2)

        with patch(
            "app.knowledge.equivalence.strategies.semantic_llm._call_llm",
            new_callable=AsyncMock,
            return_value=llm_response,
        ):
            result = await semantic_llm_checker([c1, c2])

        assert result is None


class TestSemanticLLMFailure:
    @pytest.mark.asyncio
    async def test_provider_error_returns_none_no_raise(self):
        """LLM provider error → 不抛异常, 返 None."""
        c1 = _make_candidate("字段A描述", cand_id=1)
        c2 = _make_candidate("字段B描述", cand_id=2)

        with patch(
            "app.knowledge.equivalence.strategies.semantic_llm._call_llm",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM provider unavailable"),
        ):
            result = await semantic_llm_checker([c1, c2])

        assert result is None


class TestSemanticLLMBudget:
    @pytest.mark.asyncio
    async def test_budget_exhausted_skips_without_calling_llm(self):
        """预算用尽 → 直接返 None, 不发 LLM 请求."""
        # 耗尽预算
        _SemanticBudget.exhaust()

        c1 = _make_candidate("描述A", cand_id=1)
        c2 = _make_candidate("描述B", cand_id=2)

        mock_llm = AsyncMock(return_value='{"equivalent": true, "confidence": 0.9, "reason": "same"}')
        with patch(
            "app.knowledge.equivalence.strategies.semantic_llm._call_llm",
            mock_llm,
        ):
            result = await semantic_llm_checker([c1, c2])

        assert result is None
        mock_llm.assert_not_called()
