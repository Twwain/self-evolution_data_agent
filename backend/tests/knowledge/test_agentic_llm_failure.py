"""Agentic LLM 失败耗尽负路径测试 — 验证已 emit 对象不丢失 (§6.1)."""
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from app.knowledge.extraction_agent import run_extraction_agent

pytestmark = pytest.mark.asyncio


@dataclass
class _MockToolCall:
    id: str
    name: str
    input: dict
    parse_error: str | None = None


@dataclass
class _MockToolUseResponse:
    text: str = ""
    tool_calls: list = None


_MOCK_EMIT_RESPONSE = _MockToolUseResponse(
    text="",
    tool_calls=[
        _MockToolCall(
            id="call_1",
            name="emit_schema_object",
            input={
                "paradigm": "relational", "kind": "table", "name": "users",
                "fields": [
                    {"name": "id", "type": "Long"},
                    {"name": "name", "type": "String"},
                ],
                "source_ref": "User.java:1",
            },
        )
    ],
)


async def test_llm_failure_preserves_emitted_objects():
    """mock chat_completion_with_tools: 1 emit → 第 2 次 raise → status=failed, objects 保留."""
    mock_llm = AsyncMock()
    mock_llm.side_effect = [
        _MOCK_EMIT_RESPONSE,
        RuntimeError("LLM API connection reset after 3 retries"),
    ]
    with patch("app.knowledge.extraction_agent.chat_completion_with_tools", mock_llm):
        result = await run_extraction_agent(repo_path="/tmp/fake", hint_text=None, max_iterations=10)

    assert result.status == "failed", f"预期 failed, 实际 {result.status}"
    assert result.reason == "llm_call_failed"
    assert len(result.objects) >= 1, f"应保留已 emit 对象, 实际 {len(result.objects)}"
    assert "users" in [o.get("name") for o in result.objects]


async def test_llm_failure_no_emit_yields_empty():
    """LLM 首次调用即失败 → status=failed + objects 为空."""
    mock_llm = AsyncMock(side_effect=RuntimeError("LLM API unavailable"))
    with patch("app.knowledge.extraction_agent.chat_completion_with_tools", mock_llm):
        result = await run_extraction_agent(repo_path="/tmp/fake", hint_text=None, max_iterations=10)

    assert result.status == "failed"
    assert result.reason == "llm_call_failed"
    assert len(result.objects) == 0


async def test_parse_error_fed_back_to_llm():
    """tc.parse_error 非空时跳过工具执行, JSON_PARSE_FAILED 喂回 LLM 自愈."""
    parse_error_msg = (
        "工具参数 JSON 解析失败: Unterminated string at line 1\n"
        "↓ 完整参数:\n"
        '{"paradigm": "document", "name": "product"'
    )
    mock_llm = AsyncMock()
    mock_llm.side_effect = [
        _MockToolUseResponse(
            text="",
            tool_calls=[
                _MockToolCall(
                    id="call_broken",
                    name="emit_schema_object",
                    input={},
                    parse_error=parse_error_msg,
                )
            ],
        ),
        _MockToolUseResponse(text="收到,缩短参数重试.", tool_calls=[]),
    ]
    with patch("app.knowledge.extraction_agent.chat_completion_with_tools", mock_llm):
        result = await run_extraction_agent(repo_path="/tmp/fake", hint_text=None, max_iterations=10)

    assert result.status == "ok"
    assert mock_llm.call_count == 2
    second_call_messages = mock_llm.call_args_list[1][1]["messages"]
    tool_results = [m for m in second_call_messages if m["role"] == "tool"]
    assert len(tool_results) == 1
    assert "JSON_PARSE_FAILED" in tool_results[0]["content"]
    assert "Unterminated string" in tool_results[0]["content"]
