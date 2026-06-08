import asyncio
import json

import pytest

from app.engine.sse_manager import (
    deregister_sse_session,
    format_sse_event,
    get_correction_queue,
    get_event_queue,
    list_active_trace_ids,
    register_sse_session,
)


def test_register_creates_two_queues():
    eq, cq = register_sse_session("t1")
    assert isinstance(eq, asyncio.Queue)
    assert isinstance(cq, asyncio.Queue)
    assert get_event_queue("t1") is eq
    assert get_correction_queue("t1") is cq
    deregister_sse_session("t1")  # cleanup


def test_deregister_removes_both_queues():
    register_sse_session("t2")
    deregister_sse_session("t2")
    assert get_event_queue("t2") is None
    assert get_correction_queue("t2") is None


def test_list_active_trace_ids_includes_registered():
    register_sse_session("t3")
    assert "t3" in list_active_trace_ids()
    deregister_sse_session("t3")
    assert "t3" not in list_active_trace_ids()


def test_format_sse_event_protocol():
    result = format_sse_event("tool_use", {"name": "lookup_knowledge"})
    assert result.startswith("event: tool_use\n")
    assert "data: " in result
    assert result.endswith("\n\n")
    data_line = [line for line in result.split("\n") if line.startswith("data: ")][0]
    data = json.loads(data_line[6:])
    assert data["name"] == "lookup_knowledge"


def test_format_sse_keepalive_no_data():
    result = format_sse_event("keepalive")
    assert "event: keepalive" in result
    assert result.endswith("\n\n")


def test_format_sse_handles_non_serializable():
    from datetime import datetime
    result = format_sse_event("test", {"ts": datetime(2026, 1, 1)})
    assert "event: test" in result  # default=str fallback, should not raise


def test_register_duplicate_raises():
    register_sse_session("dup")
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_sse_session("dup")
    finally:
        deregister_sse_session("dup")
