"""Stage 2 抓手 B — recall_window 内存窗口单元测试."""
from app.engine.recall_window import (
    MemoryBackend,
    set_backend,
    window_consume_next_call,
    window_pop,
    window_record,
    window_size,
)


def _fresh_backend():
    """每个测试用干净的 MemoryBackend, 防跨测试污染."""
    backend = MemoryBackend()
    set_backend(backend)
    return backend


def test_record_and_pop_basic():
    _fresh_backend()
    window_record("trace-1", [10, 20, 30])
    snap = window_pop("trace-1")
    assert snap is not None
    assert snap["recall_inc"] == {10: 1, 20: 1, 30: 1}
    assert snap["adopted_inc"] == {}
    assert snap["negative_inc"] == {}
    assert window_size() == 0  # 已 pop


def test_consume_adopting_tool_marks_adopted():
    _fresh_backend()
    window_record("trace-2", [100])
    window_consume_next_call("trace-2", "execute_query")
    snap = window_pop("trace-2")
    assert snap is not None
    assert snap["adopted_inc"] == {100: 1}
    assert snap["negative_inc"] == {}


def test_consume_negative_tool_marks_negative():
    _fresh_backend()
    window_record("trace-3", [200])
    window_consume_next_call("trace-3", "fetch_schema")
    snap = window_pop("trace-3")
    assert snap is not None
    assert snap["negative_inc"] == {200: 1}
    assert snap["adopted_inc"] == {}


def test_lookup_after_lookup_does_not_consume():
    """连续两次 lookup_knowledge: 第一次的 pending 不应被第二次 consume."""
    _fresh_backend()
    window_record("trace-4", [300])
    window_consume_next_call("trace-4", "lookup_knowledge")
    window_consume_next_call("trace-4", "execute_query")
    snap = window_pop("trace-4")
    assert snap is not None
    assert snap["adopted_inc"] == {300: 1}


def test_pop_unknown_trace_returns_none():
    _fresh_backend()
    assert window_pop("never-recorded") is None
