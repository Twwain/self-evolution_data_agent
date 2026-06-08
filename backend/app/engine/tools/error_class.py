"""Error_Class 归一化 + 滑动窗口 (设计 A6/A8).

用于 agent_loop 的「同类错误重复 → Forced_Clarify」机制。
"""
from __future__ import annotations

import re
from collections import deque

from app.config import settings
from app.logging_config import get_logger

log = get_logger("error_class")

# 消息归一化: 剥离引号字面量 / ObjectId / 裸数字, 保留稳定的错误骨架
_OBJECTID_RE = re.compile(r"ObjectId\([^)]*\)")
_QUOTED_RE = re.compile(r"['\"][^'\"]*['\"]")
_NUM_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")


def is_error_output(status: str, output: object) -> bool:
    """判定一次 tool 结果是否为错误形态 (修复 #1).

    两种来源:
      1. status == "error" — _exec_tool 兜底捕获的未包装异常 (如 pymongo OperationFailure)
      2. status == "ok" 但 output 含 "error" 键 — data_access_tools 把 DriverError /
         _ds_not_found_error / invalid_target 作为正常返回值下发 (status 仍是 ok)
    """
    if status == "error":
        return True
    return isinstance(output, dict) and "error" in output


def _msg_signature(message: str) -> str:
    """把错误消息归一化为稳定特征 (末位兜底分支用)。"""
    s = message.lower()
    s = _OBJECTID_RE.sub("", s)
    s = _QUOTED_RE.sub("", s)
    s = _NUM_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip()
    return s[: settings.agent_loop_error_class_msg_signature_len]


def normalize_error_class(output: object) -> str | None:
    """把错误形态的 tool 输出归一化为稳定的 Error_Class 签名 (R5.1/R5.2).

    返回 None 表示该输出不是可归类的错误形态。调用方应先用 is_error_output 判定。

    优先级 (从高到低):
      1. 数值 error_code → f"{error_type}:{error_code}"  (来自 _exec_tool, 如 OperationFailure)
      2. DriverError 形态 {"error": <str_code>} → f"{error}"  (字符串码本身稳定, 不做正则)
      3. 末位兜底 → f"{error_type}:{msg_signature}" (无数值码也非 DriverError, 记 log.warning)
    """
    if not isinstance(output, dict):
        return None

    # 规则 1: 数值错误码 (异常包装形态, error_type + error_code)
    code = output.get("error_code")
    if isinstance(code, (int, str)) and str(code) != "":
        error_type = output.get("error_type") or output.get("error") or "Error"
        return f"{error_type}:{code}"

    # 规则 2: DriverError 形态 {"error": <str_code>, "message": ...}
    if "error" in output and "error_type" not in output:
        err_code = output.get("error")
        if isinstance(err_code, str) and err_code:
            return err_code

    # 规则 3: 末位兜底
    if "error_type" in output or "error" in output or "error_message" in output:
        error_type = output.get("error_type") or output.get("error") or "Error"
        message = str(output.get("error_message") or output.get("message") or "")
        log.warning(
            "[error_class] 无结构化错误码, 退化到消息签名: error_type=%s", error_type
        )
        return f"{error_type}:{_msg_signature(message)}"

    return None


class ErrorClassWindow:
    """Agent_Loop 内按 Error_Class 计数的滑动窗口 (R5.3/R5.4/R5.12).

    每个 tool 结果占一格; 成功结果压 None 占位 (会把更旧错误挤出, 但不主动清零他类)。
    """

    def __init__(self, size: int) -> None:
        self._dq: deque[str | None] = deque(maxlen=size)

    def record(self, error_class: str | None) -> None:
        """压入一次 tool 结果的 Error_Class (成功 → None)。"""
        self._dq.append(error_class)

    def count(self, error_class: str) -> int:
        """该 Error_Class 在当前窗口内的出现次数 (窗口外历史不计)。"""
        return sum(1 for x in self._dq if x == error_class)

    def reset_class(self, error_class: str) -> None:
        """清零某 Error_Class 计数 (R5.8 用户回应后): 移除窗口内该类所有记录。"""
        self._dq = deque(
            (x for x in self._dq if x != error_class), maxlen=self._dq.maxlen
        )

    def first_over_threshold(self, threshold: int) -> str | None:
        """返回当前窗口内首个达到 threshold 次的 Error_Class, 无则 None。"""
        seen: set[str] = set()
        for x in self._dq:
            if x is None or x in seen:
                continue
            seen.add(x)
            if self.count(x) >= threshold:
                return x
        return None
