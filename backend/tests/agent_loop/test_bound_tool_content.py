"""上下文预算护栏 — bound_tool_content 回喂 LLM 的 tool 结果字符封顶.

不变量:
- ≤预算 → 原样透传 (byte-identical, 正常结果零回归)
- >预算 → 返回串恒 ≤ budget, 入参 output 绝不被修改 (tool_trace 完整性)
- 通用: 不假设字段名, 对 mongo/mysql/count/error 等任意形态都生效
"""
import copy
import json

from app.engine.agent_loop import _stringify, bound_tool_content


# ── 病态形态: $group 把 8406 条文本收进单行数组 (row_count=1 却 >1M chars) ──
def _pathological_mongo_output() -> dict:
    sentence = (
        "The quick brown fox jumps over the lazy dog while the sun sets slowly "
        "behind the distant rolling hills of the quiet countryside today."
    )
    return {
        "rows": [{"_id": None, "sentences": [sentence] * 8406, "words": [f"w{i}" for i in range(8406)]}],
        "row_count": 1,
        "truncated": False,
        "elapsed_ms": 412,
    }


def test_oversized_output_bounded_below_budget():
    out = _pathological_mongo_output()
    budget = 500_000
    assert len(_stringify(out)) > 1_000_000  # 确认确实超预算
    bounded = bound_tool_content(out, budget_chars=budget)
    assert len(bounded) <= budget


def test_oversized_output_does_not_mutate_caller():
    out = _pathological_mongo_output()
    snapshot = copy.deepcopy(out)
    bound_tool_content(out, budget_chars=500_000)
    assert out == snapshot, "护栏修改了入参 output — tool_trace 会被污染"


def test_oversized_output_preserves_metadata_and_marker():
    out = _pathological_mongo_output()
    bounded = bound_tool_content(out, budget_chars=500_000)
    parsed = json.loads(bounded)
    assert parsed["_context_truncated"] is True
    assert "_truncation_note" in parsed
    assert parsed["row_count"] == 1  # 标量元数据必须存活


def test_list_sampling_keeps_head_and_omitted_count():
    # 单行大数组 — 收缩后 list 仅留头 5 + _omitted_elements 计数. 预算够大,
    # 收缩后的 JSON 不触发硬截, 保持合法可解析.
    out = {"rows": [{"sentences": ["s" * 5000 for _ in range(100)]}], "row_count": 1}
    assert len(_stringify(out)) > 500_000
    bounded = bound_tool_content(out, budget_chars=500_000)
    assert len(bounded) <= 500_000
    parsed = json.loads(bounded)
    sentences = parsed["rows"][0]["sentences"]
    assert len(sentences) == 6  # 头 5 + 1 个 _omitted_elements marker
    assert sentences[-1] == {"_omitted_elements": 95}


def test_normal_outputs_pass_through_identically():
    cases = [
        {"rows": [{"categoryName": f"类目{i}", "count": i} for i in range(20)],
         "row_count": 20, "truncated": False, "elapsed_ms": 31},
        {"rows": [{"count": 14783}], "row_count": 1, "truncated": False, "elapsed_ms": 5},
        {"rows": [{"id": i, "name": f"u{i}"} for i in range(1000)],
         "row_count": 1000, "truncated": True, "elapsed_ms": 88},
        {"error_type": "OperationFailure", "error_message": "boom", "error_code": 16410},
    ]
    for out in cases:
        s = _stringify(out)
        assert len(s) <= 500_000  # 正常结果本就在预算内
        assert bound_tool_content(out, budget_chars=500_000) == s


def test_generic_across_db_shapes_mysql():
    # MySQL 形态的超大结果 — 同一护栏, 无 db 专属代码
    big = {
        "rows": [{"id": i, "bio": "x" * 80} for i in range(50_000)],
        "row_count": 50_000,
        "truncated": False,
        "elapsed_ms": 120,
    }
    bounded = bound_tool_content(big, budget_chars=500_000)
    assert len(bounded) <= 500_000
    parsed = json.loads(bounded)
    assert parsed["_context_truncated"] is True
    assert parsed["row_count"] == 50_000


def test_non_dict_oversized_output_hard_cut():
    out = "x" * 2_000_000  # str 形态 (非 dict)
    bounded = bound_tool_content(out, budget_chars=1000)
    assert len(bounded) <= 1000 + len('...<truncated>"}')
