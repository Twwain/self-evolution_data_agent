"""Error_Class 归一化 + 窗口测试 (Properties 16, 17, 32; tasks 12.2/12.2b/12.4).

cd backend && python -m pytest tests/agent_loop/test_error_class.py --timeout=120 --timeout-method=thread
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.engine.tools.error_class import (
    ErrorClassWindow,
    is_error_output,
    normalize_error_class,
)


# ════════════════════════════════════════════
#  normalize_error_class (Property 16)
# ════════════════════════════════════════════

# Feature: mongo-flavor-capabilities-and-error-clarify, Property 16: Error_Class 归一化全覆盖且确定
@settings(max_examples=100)
@given(
    error_type=st.text(min_size=1, max_size=10, alphabet="ABCdef"),
    code=st.one_of(st.integers(), st.text(min_size=1, max_size=6)),
)
def test_property_16_rule1_numeric_code(error_type, code):
    out = {"error_type": error_type, "error_message": "x", "error_code": code}
    sig1 = normalize_error_class(out)
    sig2 = normalize_error_class(dict(out))
    assert sig1 is not None
    assert sig1 == sig2
    assert sig1 == f"{error_type}:{code}"


def test_property_16_rule2_drivererror():
    out = {"error": "payload_shape_mismatch", "message": "bad", "suggestion": "fix"}
    assert normalize_error_class(out) == "payload_shape_mismatch"


def test_property_16_rule3_fallback_stable():
    out = {"error_type": "OperationFailure", "error_message": "boom at ObjectId('abc') id=42 'lit'"}
    sig = normalize_error_class(out)
    assert sig is not None and sig.startswith("OperationFailure:")
    # 数字/ObjectId/引号字面量被剥离 → 同骨架不同实例稳定
    out2 = {"error_type": "OperationFailure", "error_message": "boom at ObjectId('zzz') id=99 'other'"}
    assert normalize_error_class(out2) == sig


def test_normalize_documentdb_three_classes():
    assert normalize_error_class({"error_type": "OperationFailure", "error_code": 5654600}) == "OperationFailure:5654600"
    assert normalize_error_class({"error_type": "OperationFailure", "error_code": 304}) == "OperationFailure:304"
    assert normalize_error_class({"error_type": "OperationFailure", "error_code": 16410}) == "OperationFailure:16410"


def test_normalize_non_error_returns_none():
    assert normalize_error_class({"rows": [], "row_count": 0}) is None
    assert normalize_error_class("not a dict") is None


# ════════════════════════════════════════════
#  is_error_output (Property 32 — 修复 #1)
# ════════════════════════════════════════════

# Feature: mongo-flavor-capabilities-and-error-clarify, Property 32: 错误形态识别覆盖 status==ok 含 error 键
@settings(max_examples=100)
@given(
    has_error_key=st.booleans(),
    status=st.sampled_from(["ok", "error"]),
)
def test_property_32_is_error_output(has_error_key, status):
    output = {"error": "payload_shape_mismatch", "message": "x"} if has_error_key else {"rows": []}
    result = is_error_output(status, output)
    expected = (status == "error") or has_error_key
    assert result == expected


def test_property_32_drivererror_as_ok_dict_counts():
    # data_access_tools 把 DriverError 作正常返回值: status=ok 但含 error 键
    assert is_error_output("ok", {"error": "payload_shape_mismatch", "message": "x"}) is True
    # 规则 2 非死代码: 该形态可被 normalize
    assert normalize_error_class({"error": "payload_shape_mismatch", "message": "x"}) == "payload_shape_mismatch"


# ════════════════════════════════════════════
#  ErrorClassWindow (Property 17)
# ════════════════════════════════════════════

# Feature: mongo-flavor-capabilities-and-error-clarify, Property 17: 错误类窗口计数语义
@settings(max_examples=100)
@given(
    size=st.integers(min_value=1, max_value=8),
    seq=st.lists(st.one_of(st.none(), st.sampled_from(["A", "B", "C"])), max_size=30),
)
def test_property_17_window_count(size, seq):
    w = ErrorClassWindow(size)
    for x in seq:
        w.record(x)
    # count(c) 恒等于最近 size 格内 c 的次数
    tail = seq[-size:]
    for c in ("A", "B", "C"):
        assert w.count(c) == sum(1 for x in tail if x == c)


def test_window_reset_class():
    w = ErrorClassWindow(5)
    for x in ["A", "A", "B"]:
        w.record(x)
    assert w.count("A") == 2
    w.reset_class("A")
    assert w.count("A") == 0
    assert w.count("B") == 1  # 不影响他类


def test_window_success_does_not_reset_others():
    w = ErrorClassWindow(5)
    w.record("A")
    w.record(None)  # 成功
    w.record("A")
    assert w.count("A") == 2  # None 占位不清零 A


def test_first_over_threshold():
    w = ErrorClassWindow(5)
    for x in ["A", "B", "A"]:
        w.record(x)
    assert w.first_over_threshold(2) == "A"
    assert w.first_over_threshold(3) is None
