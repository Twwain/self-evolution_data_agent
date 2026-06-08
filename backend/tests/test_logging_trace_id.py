"""logging_config 双 trace_id 注入测试.

验证 TraceIdFilter 同时注入业务 trace_id (contextvar) 和 langfuse trace_id (OTel span context).
"""

from __future__ import annotations

import logging

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import set_tracer_provider

from app.logging_config import TraceIdFilter, _current_lf_trace_id, trace_id_var


def _make_record() -> logging.LogRecord:
    return logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=0,
        msg="m", args=(), exc_info=None,
    )


def test_filter_no_otel_span_no_business_id():
    """无业务 trace_id + 无活跃 OTel span: 两列都 '-'."""
    f = TraceIdFilter()
    rec = _make_record()
    f.filter(rec)
    assert getattr(rec, "trace_id") == "-"
    assert getattr(rec, "lf_trace_id") == "-"


def test_filter_business_id_only():
    """有业务 trace_id, 无 OTel span: trace 实, lf '-'."""
    token = trace_id_var.set("biz-uuid-123")
    try:
        f = TraceIdFilter()
        rec = _make_record()
        f.filter(rec)
        assert getattr(rec, "trace_id") == "biz-uuid-123"
        assert getattr(rec, "lf_trace_id") == "-"
    finally:
        trace_id_var.reset(token)


def test_filter_otel_span_yields_lf_trace_id():
    """有活跃 OTel span: lf_trace_id 是 32 hex."""
    set_tracer_provider(TracerProvider())
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span("test"):
        f = TraceIdFilter()
        rec = _make_record()
        f.filter(rec)
        lf_id = getattr(rec, "lf_trace_id")
        assert lf_id != "-"
        assert len(lf_id) == 32
        # 32 hex 校验
        int(lf_id, 16)


def test_current_lf_trace_id_helper_outside_span():
    """helper 在无 span 时返 '-' 不抛."""
    assert _current_lf_trace_id() == "-"


def test_current_lf_trace_id_helper_inside_span():
    """helper 在 span 内返合法 32 hex."""
    set_tracer_provider(TracerProvider())
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span("test"):
        v = _current_lf_trace_id()
        assert len(v) == 32
        int(v, 16)
