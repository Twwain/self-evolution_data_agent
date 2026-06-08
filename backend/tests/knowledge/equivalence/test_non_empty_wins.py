"""T2 — non_empty_wins strategy 单测.

移植自 canonical_promote.py:_try_non_empty_wins 的回归 case.
"""

import json

from app.knowledge.equivalence.strategies.non_empty_wins import non_empty_wins_checker


def _make_candidate(description: str, cand_id: int = 1):
    """构造最小 SchemaCanonicalCandidate mock, 仅含 description 字段."""

    class FakeCandidate:
        def __init__(self, desc, id_):
            self.id = id_
            self.candidate_value_json = json.dumps({"description": desc}, ensure_ascii=False)

    return FakeCandidate(description, cand_id)


class TestNonEmptyWins:
    def test_one_empty_one_non_empty_picks_non_empty(self):
        c1 = _make_candidate("", 1)
        c2 = _make_candidate("用户订单状态", 2)
        result = non_empty_wins_checker([c1, c2])
        assert result is not None
        winner, reason = result
        assert winner.id == 2
        assert reason == "non_empty_wins"

    def test_all_empty_returns_first(self):
        c1 = _make_candidate("", 1)
        c2 = _make_candidate("   ", 2)  # 仅空格也算空
        result = non_empty_wins_checker([c1, c2])
        assert result is not None
        winner, _ = result
        assert winner.id == 1  # 任取第一个

    def test_multiple_non_empty_returns_none(self):
        """多个非空且不同 → 让链继续到下一条 rule (mongo_struct / semantic_llm)."""
        c1 = _make_candidate("订单状态", 1)
        c2 = _make_candidate("订单当前状态", 2)
        result = non_empty_wins_checker([c1, c2])
        assert result is None
