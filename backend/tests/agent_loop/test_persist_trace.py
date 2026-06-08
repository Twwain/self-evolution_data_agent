"""Stage 2 抓手 E — _persist_trace 单元测试.

直接调用 _persist_trace 验证 AgentTrace 行正确写入.
使用 db_session fixture (SAVEPOINT rollback 隔离).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from bson import ObjectId
from bson.timestamp import Timestamp as BsonTimestamp
from sqlalchemy import select

from app.engine.agent_loop import _persist_trace
from app.models import AgentTrace


@pytest.mark.asyncio
async def test_completed_trace_persisted(db_session):
    """_persist_trace status=completed 正确写入."""
    await _persist_trace(
        db=db_session,
        trace_id="persist-test-completed",
        namespace_id=None,
        user_query="本月订单数",
        tool_trace=[{"name": "lookup_knowledge", "input": {}, "output": {}}],
        reflection_log=[{"confidence": 0.9, "reason": "ok"}],
        status="completed",
    )

    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "persist-test-completed")
    )).scalar_one()
    assert row.status == "completed"
    assert row.user_query == "本月订单数"
    assert '"lookup_knowledge"' in row.trace_json
    assert "confidence" in row.reflection_log_json


@pytest.mark.asyncio
async def test_cancelled_trace_persisted(db_session):
    """_persist_trace status=cancelled 正确写入."""
    await _persist_trace(
        db=db_session,
        trace_id="persist-test-cancelled",
        namespace_id=None,
        user_query="取消的查询",
        tool_trace=[],
        reflection_log=[],
        status="cancelled",
    )

    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "persist-test-cancelled")
    )).scalar_one()
    assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_failed_trace_persisted(db_session):
    """_persist_trace status=failed 正确写入."""
    await _persist_trace(
        db=db_session,
        trace_id="persist-test-failed",
        namespace_id=None,
        user_query="失败的查询",
        tool_trace=[{"name": "fetch_schema", "input": {}, "output": "error"}],
        reflection_log=[],
        status="failed",
    )

    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "persist-test-failed")
    )).scalar_one()
    assert row.status == "failed"
    assert "fetch_schema" in row.trace_json


# ════════════════════════════════════════════
#  回归: BSON Timestamp / datetime / Decimal / ObjectId 等非 JSON 原生类型
#  根因: tool_trace 含 mongo driver 原始返回, json.dumps 默认抛 TypeError →
#  agent_traces 表常年空 (production RDS 实测 0 行).
#  修复: _persist_trace 两处 json.dumps 加 default=str.
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bson_timestamp_in_tool_output_serializes(db_session):
    """tool_trace 嵌入 bson.Timestamp 不再抛 'not JSON serializable'."""
    bson_ts = BsonTimestamp(1779776405, 1)  # 2026-05-26 06:20:05 UTC
    await _persist_trace(
        db=db_session,
        trace_id="persist-test-bson-timestamp",
        namespace_id=None,
        user_query="带 BSON Timestamp 的查询",
        tool_trace=[{
            "name": "execute_query",
            "input": {"target": "c_product", "mode": "single"},
            "output": {"rows": [{"_id": ObjectId(), "ts": bson_ts}]},
            "status": "ok",
        }],
        reflection_log=[],
        status="completed",
    )
    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "persist-test-bson-timestamp")
    )).scalar_one()
    assert row.status == "completed"
    assert "execute_query" in row.trace_json


@pytest.mark.asyncio
async def test_datetime_and_decimal_in_reflection_serializes(db_session):
    """reflection_log 含 datetime / Decimal 不再抛序列化错误."""
    await _persist_trace(
        db=db_session,
        trace_id="persist-test-datetime-decimal",
        namespace_id=None,
        user_query="带 datetime/Decimal 的反思",
        tool_trace=[],
        reflection_log=[{
            "confidence": Decimal("0.85"),
            "at": datetime(2026, 5, 26, 13, 32, 19, tzinfo=timezone.utc),
            "reason": "ok",
        }],
        status="completed",
    )
    row = (await db_session.execute(
        select(AgentTrace).where(AgentTrace.trace_id == "persist-test-datetime-decimal")
    )).scalar_one()
    assert row.status == "completed"
    assert "0.85" in row.reflection_log_json
    assert "2026-05-26" in row.reflection_log_json
