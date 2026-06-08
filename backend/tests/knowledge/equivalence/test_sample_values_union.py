"""T2 — sample_values_union strategy 单测.

移植自 canonical_promote.py:_union_sample_values 的回归 case.
"""

import json

from app.knowledge.equivalence.strategies.sample_values_union import (
    sample_values_union_checker,
)


def _make_candidate(samples: list, cand_id: int = 1):
    """构造最小 SchemaCanonicalCandidate mock, 仅含 sample_values 字段."""

    class FakeCandidate:
        def __init__(self, sv, id_):
            self.id = id_
            self.candidate_value_json = json.dumps({"sample_values": sv}, ensure_ascii=False)

    return FakeCandidate(samples, cand_id)


class TestSampleValuesUnion:
    def test_no_overlap_union(self):
        """无重叠: 并集是两边相加."""
        c1 = _make_candidate(["pending", "shipped"], 1)
        c2 = _make_candidate(["delivered", "cancelled"], 2)
        result = sample_values_union_checker([c1, c2])
        assert result is not None
        winner, reason = result
        assert reason == "sample_values_union"
        assert winner.id == 1  # 选 cands[0] 作为载体
        merged = json.loads(winner.candidate_value_json)["sample_values"]
        assert set(merged) == {"pending", "shipped", "delivered", "cancelled"}

    def test_overlap_dedup(self):
        """有重叠: 并集去重."""
        c1 = _make_candidate(["a", "b", "c"], 1)
        c2 = _make_candidate(["b", "c", "d"], 2)
        result = sample_values_union_checker([c1, c2])
        assert result is not None
        winner, _ = result
        merged = json.loads(winner.candidate_value_json)["sample_values"]
        assert set(merged) == {"a", "b", "c", "d"}
        assert len(merged) == 4  # b/c 各只出现一次

    def test_dict_values_union(self):
        """非 scalar sample (如 dict) 也支持去重 (json.dumps sort_keys 作 key)."""
        c1 = _make_candidate([{"k": 1}, {"k": 2}], 1)
        c2 = _make_candidate([{"k": 2}, {"k": 3}], 2)
        result = sample_values_union_checker([c1, c2])
        assert result is not None
        winner, _ = result
        merged = json.loads(winner.candidate_value_json)["sample_values"]
        assert len(merged) == 3
