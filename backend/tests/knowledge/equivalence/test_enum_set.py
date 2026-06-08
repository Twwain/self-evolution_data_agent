"""T2 — enum_set strategy 单测.

移植自 canonical_promote.py:_try_enum_set_equivalent 的回归 case.
"""

import json

import pytest

from app.knowledge.equivalence.strategies.enum_set import enum_set_checker


def _make_candidate(enum_values: list[dict], cand_id: int = 1):
    """构造最小 SchemaCanonicalCandidate mock."""

    class FakeCandidate:
        def __init__(self, values, id_):
            self.id = id_
            self.candidate_value_json = json.dumps({"enum_values": values}, ensure_ascii=False)

    return FakeCandidate(enum_values, cand_id)


class TestEnumSetEquivalent:
    def test_superset_selected_when_one_candidate_is_full_set(self):
        """一个 candidate 是全集 → 选它."""
        c1 = _make_candidate([
            {"name": "ACTIVE", "db_value": 1},
            {"name": "INACTIVE", "db_value": 0},
        ], cand_id=1)
        c2 = _make_candidate([
            {"name": "ACTIVE", "db_value": 1},
        ], cand_id=2)

        result = enum_set_checker([c1, c2])
        assert result is not None
        winner, reason = result
        assert winner.id == 1
        assert reason == "enum_superset_selected"

    def test_superset_synthesized_when_no_candidate_is_full_set(self):
        """无 candidate 是全集 → 合成并集写入 cands[0]."""
        c1 = _make_candidate([
            {"name": "ACTIVE", "db_value": 1},
        ], cand_id=1)
        c2 = _make_candidate([
            {"name": "INACTIVE", "db_value": 0},
        ], cand_id=2)

        result = enum_set_checker([c1, c2])
        assert result is not None
        winner, reason = result
        assert winner.id == 1
        assert reason == "enum_superset_synthesized"
        # 验证并集已写入
        merged = json.loads(winner.candidate_value_json)["enum_values"]
        names = {item["name"] for item in merged}
        assert names == {"ACTIVE", "INACTIVE"}


class TestEnumSetConflict:
    def test_same_name_different_db_value_returns_none(self):
        """同 name 不同 db_value → 真冲突, 返 None."""
        c1 = _make_candidate([
            {"name": "ACTIVE", "db_value": 1},
        ], cand_id=1)
        c2 = _make_candidate([
            {"name": "ACTIVE", "db_value": "Y"},
        ], cand_id=2)

        result = enum_set_checker([c1, c2])
        assert result is None
