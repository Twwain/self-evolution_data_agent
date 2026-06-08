"""INV-ERRCODE on BOTH the direct and plan paths (Property 8; design §4.6; task 8.3).

钉死 INV-ERRCODE 承重不变量在两条到驱动层的路径上都成立:

  - 直查路径 (direct `execute_query` tool): 原始 pymongo `OperationFailure` 透传,
    `_exec_tool` 的 `getattr(e, "code", None)` 取到数值码 → normalize_error_class 走规则 1
    得到稳定数值键 `OperationFailure:16410` (回归护栏).

  - Plan 路径 (`execute_plan` → `PlanExecutionError`): §4.6 让 `PlanExecutionError.code`
    重新暴露 cause 的数值码, 于是经 `_exec_tool` 后同样得到数值键 (`PlanExecutionError:16410`)。
    历史上 `PlanExecutionError` 没有 `.code` → 数值码丢失 → Error_Class 退化到散文签名,
    这是 INV-ERRCODE 在两条路径之一上的静默违反, 本测试在其回归时立即变红。

  - 无数值码的 cause (capability-violation RuntimeError / datasource-not-found):
    `PlanExecutionError.code is None`, `_exec_tool` 输出不含 `error_code`,
    normalize_error_class 退回散文签名 (规则 3), 而非数值键 — 行为不变。

测试直接调用真实的 `_exec_tool` (而非手工拼 output dict), 以覆盖 agent_loop 里
`getattr(e, "code", None)` 的真实恢复逻辑。

cd backend && python -m pytest tests/agent_loop/test_inv_errcode_both_paths.py \
    --timeout=120 --timeout-method=thread
"""
from __future__ import annotations

import asyncio

import pytest
from pymongo.errors import OperationFailure

from app.engine.agent_loop import _exec_tool
from app.engine.llm import ToolCall
from app.engine.plan_executor import PlanExecutionError
from app.engine.tools.error_class import _msg_signature, normalize_error_class

# Feature: mongo-count-pipeline-and-plan-caps — 真实 DocumentDB ds=3 / trace c85ccb16 的码
_OF_CODE = 16410


def _raising_tool(exc: BaseException):
    """构造一个无视入参、只抛 `exc` 的异步 fake tool fn (供 _exec_tool 执行)."""

    async def _fn(**_kwargs):
        raise exc

    return _fn


async def _run_exec_tool(exc: BaseException, tool_name: str = "execute_plan") -> dict:
    """经真实 _exec_tool 兜底链跑一个会抛 `exc` 的工具, 返回其 output dict 协议结果."""
    tc = ToolCall(id="c1", name=tool_name, input={})
    registry = {tool_name: _raising_tool(exc)}
    return await _exec_tool(tc, registry, asyncio.Semaphore(1))


# ══════════════════════════════════════════════════════════════════════════════
#  Plan path — PlanExecutionError 重新暴露 cause 的数值码 (§4.6)
# ══════════════════════════════════════════════════════════════════════════════

def test_plan_error_reexposes_numeric_code_attribute():
    """PlanExecutionError 包裹携码 cause → .code 直接暴露该数值码 (§4.6 additive 属性)."""
    cause = OperationFailure("Invalid $project :: $id_str", code=_OF_CODE)
    err = PlanExecutionError(step_idx=2, cause=cause, pipeline=[{"$project": {"x": "$a.$b"}}])

    assert err.code == _OF_CODE  # getattr(cause, "code", None) 恢复


@pytest.mark.asyncio
async def test_plan_path_numeric_code_survives_to_error_class():
    """Plan 路径: PlanExecutionError(code=16410) 经 _exec_tool → error_code 到达 →
    normalize_error_class 走规则 1 得到数值键 (而非散文兜底)."""
    cause = OperationFailure("Invalid $project :: $id_str", code=_OF_CODE)
    err = PlanExecutionError(step_idx=2, cause=cause)

    res = await _run_exec_tool(err)

    assert res["status"] == "error"
    # _exec_tool 的 getattr(e, "code", None) 从 PlanExecutionError.code 取到数值码
    assert res["output"]["error_code"] == _OF_CODE
    # error_type = type(e).__name__ = "PlanExecutionError"; 规则 1 → "{type}:{code}"
    assert normalize_error_class(res["output"]) == f"PlanExecutionError:{_OF_CODE}"


# ══════════════════════════════════════════════════════════════════════════════
#  Code-less causes — .code is None, 退回散文签名 (规则 3), 非数值键
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_plan_path_capability_violation_has_no_numeric_code():
    """能力违规 cause 是无码 RuntimeError → .code is None → 输出无 error_code →
    normalize_error_class 退回散文签名 (规则 3), 不产生数值键 (R6: 靠 reason+建议 文案)."""
    # 形如 plan_executor._cap_error 拼出的 reason + 建议 文案 (无 pymongo 数值码)
    cause = RuntimeError("目标数据源不支持 project_no_dollar_fieldpath ... 建议: 字段引用用裸名, 不要嵌入 $")
    err = PlanExecutionError(step_idx=1, cause=cause)

    assert err.code is None

    res = await _run_exec_tool(err)

    assert res["status"] == "error"
    assert "error_code" not in res["output"]  # 无数值码 → 不注入 error_code
    # 规则 3 散文兜底: f"{error_type}:{_msg_signature(message)}", 数字被 _msg_signature 剥除
    expected = f"PlanExecutionError:{_msg_signature(res['output']['error_message'])}"
    assert normalize_error_class(res["output"]) == expected
    # 显式确认不是数值键形态 (与 §4.6 的 16410 数值键互斥)
    assert not normalize_error_class(res["output"]).endswith(f":{_OF_CODE}")


@pytest.mark.asyncio
async def test_plan_path_datasource_not_found_has_no_numeric_code():
    """datasource-not-found cause 同样是无码 RuntimeError → .code is None → 散文兜底."""
    cause = RuntimeError("未找到 database=catalog_db 的 mongo datasource (ns=1)")
    err = PlanExecutionError(step_idx=1, cause=cause)

    assert err.code is None

    res = await _run_exec_tool(err)

    assert res["status"] == "error"
    assert "error_code" not in res["output"]
    expected = f"PlanExecutionError:{_msg_signature(res['output']['error_message'])}"
    assert normalize_error_class(res["output"]) == expected


# ══════════════════════════════════════════════════════════════════════════════
#  Direct path — 原始 OperationFailure 仍透传数值码 (回归护栏)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_direct_path_operationfailure_code_survives():
    """直查路径回归护栏: 原始 OperationFailure(code=16410) 经 _exec_tool 仍透出数值码 →
    normalize_error_class 走规则 1 得到 OperationFailure:16410 (不退化为消息正则)."""
    exc = OperationFailure("Invalid $project :: $id_str", code=_OF_CODE)

    res = await _run_exec_tool(exc, tool_name="execute_query")

    assert res["status"] == "error"
    assert res["output"]["error_code"] == _OF_CODE
    assert res["output"]["error_type"] == "OperationFailure"
    assert normalize_error_class(res["output"]) == f"OperationFailure:{_OF_CODE}"
