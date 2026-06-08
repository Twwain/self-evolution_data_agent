"""Stage 2 抓手 D — relations 模块单元 (真实 LLM, 不 mock).

覆盖:
1. 空近邻返空
2. 语义等价识别为 equivalent
3. 统计周期冲突识别为 conflict
4. 返回值 relation 枚举合法
"""
from app.knowledge.relations import VALID_RELATIONS, detect_relations


def test_detect_relations_empty_neighbors_returns_empty():
    assert detect_relations("foo", []) == []


def test_detect_relations_equivalent_case():
    """语义等价应识别为 equivalent."""
    new = "活跃用户指 30 天内有过登录的用户"
    neighbors = [{"id": 1, "content": "活跃用户=过去一个月内登录过的用户"}]
    out = detect_relations(new, neighbors)
    assert any(r.related_entry_id == 1 and r.relation == "equivalent" for r in out)


def test_detect_relations_conflict_case():
    """统计周期不同应识别为 conflict."""
    new = "VIP 用户指本月消费 ≥1000 元的用户"
    neighbors = [{"id": 2, "content": "VIP 用户指过去 30 天消费 ≥1000 元的用户"}]
    out = detect_relations(new, neighbors)
    assert any(r.related_entry_id == 2 and r.relation == "conflict" for r in out)


def test_detect_relations_returns_only_valid_enum():
    out = detect_relations("foo", [{"id": 99, "content": "bar"}])
    for r in out:
        assert r.relation in VALID_RELATIONS
