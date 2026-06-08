"""INV-ERRCODE 回归 + 数值码归一化 (Property 27; tasks 13.2/13.3).

钉死: pymongo OperationFailure.code 经真实 data_access_tools.execute_query → _exec_tool
链后, output["error_code"] 原样到达 (未被任何中间层包装为 DriverError 丢失数值码)。
新增 `except OperationFailure → DriverError` 包装时此测试立即变红。

cd backend && python -m pytest tests/agent_loop/test_inv_errcode.py --timeout=120 --timeout-method=thread
"""
from __future__ import annotations

import asyncio

import pytest
from pymongo.errors import OperationFailure

import app.engine.tools.data_access_tools as dat
from app.engine.agent_loop import _exec_tool
from app.engine.llm import ToolCall
from app.engine.tools.error_class import normalize_error_class


class _FakeDriver:
    async def execute_query(self, ds, target, query, mode="single", batch_size=1000):
        raise OperationFailure("Invalid $getField", code=5654600)


async def _fake_resolve_ds(db, namespace_id, db_type, database):
    return object()  # 非 None ds 占位


# Feature: mongo-flavor-capabilities-and-error-clarify, Property 27: OperationFailure 数值码原样到达 _exec_tool (INV-ERRCODE)
@pytest.mark.asyncio
async def test_property_27_operationfailure_code_survives(monkeypatch):
    monkeypatch.setattr(dat, "resolve_ds", _fake_resolve_ds)
    monkeypatch.setattr(dat, "get_driver", lambda db_type: _FakeDriver())

    # 真实调用链: data_access_tools.execute_query (OperationFailure 不是 DriverError → 原样上抛)
    #            → _exec_tool 兜底 except Exception 捕获 .code
    tc = ToolCall(id="c1", name="execute_query", input={
        "db": None, "namespace_id": 1, "db_type": "mongodb",
        "database": "d", "target": "c", "query": {"pipeline": []},
    })
    registry = {"execute_query": dat.execute_query}
    res = await _exec_tool(tc, registry, asyncio.Semaphore(1))

    assert res["status"] == "error"
    assert res["output"]["error_code"] == 5654600
    # normalize 走规则 1 (数值码), 非消息正则
    assert normalize_error_class(res["output"]) == "OperationFailure:5654600"


@pytest.mark.asyncio
async def test_drivererror_returned_as_ok_dict_still_classifiable(monkeypatch):
    """DriverError 被 data_access_tools catch → status=ok 含 error 键 (修复 #1 场景)。"""
    from app.engine.drivers._exceptions import PayloadShapeMismatchError

    class _BadDriver:
        async def execute_query(self, ds, target, query, mode="single", batch_size=1000):
            raise PayloadShapeMismatchError("bad payload", suggestion="fix")

    monkeypatch.setattr(dat, "resolve_ds", _fake_resolve_ds)
    monkeypatch.setattr(dat, "get_driver", lambda db_type: _BadDriver())

    tc = ToolCall(id="c1", name="execute_query", input={
        "db": None, "namespace_id": 1, "db_type": "mongodb",
        "database": "d", "target": "c", "query": {"pipeline": []},
    })
    res = await _exec_tool(tc, {"execute_query": dat.execute_query}, asyncio.Semaphore(1))
    # DriverError 被 catch → 正常返回值 → status=ok 但含 error 键
    assert res["status"] == "ok"
    assert res["output"]["error"] == "payload_shape_mismatch"
    from app.engine.tools.error_class import is_error_output
    assert is_error_output(res["status"], res["output"]) is True
    assert normalize_error_class(res["output"]) == "payload_shape_mismatch"
