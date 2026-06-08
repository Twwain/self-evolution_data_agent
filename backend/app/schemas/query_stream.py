"""
Stage 5 — SSE 流式查询相关请求/响应模型
前后端 SSE 通信契约, 反向通道纠偏协议, 澄清解锁协议
"""

from typing import Literal

from pydantic import BaseModel


class QueryStreamRequest(BaseModel):
    namespace_id: int
    question: str
    datasource_id: int | None = None
    session_id: str | None = None


class CorrectionRequest(BaseModel):
    step_id: str = ""
    correction_type: Literal["abort", "redirect", "param_override"]
    instruction: str | None = None
    override_input: dict | None = None


class ClarifyResponseRequest(BaseModel):
    pending_id: int   # PendingClarification.id (frontend gets it from clarify_request event)
    answer: str
