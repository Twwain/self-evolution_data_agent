"""Agentic dead_loop recovery — mock 连续相同调用 → status=partial + reason=dead_loop."""
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


_SAME_READ = _MockToolUseResponse(
    text="",
    tool_calls=[_MockToolCall(id="call_1", name="read_file", input={"path": "src/main/java/Foo.java"})],
)


async def test_dead_loop_triggers_partial():
    """连续相同 (name+input) 工具调用达窗口 → status=partial, reason=dead_loop."""
    mock_llm = AsyncMock()
    mock_llm.return_value = _SAME_READ
    with patch("app.knowledge.extraction_agent.chat_completion_with_tools", mock_llm):
        result = await run_extraction_agent(repo_path="/tmp/fake", hint_text=None, max_iterations=20)

    assert result.status == "partial", f"预期 partial, 实际 {result.status}"
    assert result.reason == "dead_loop", f"预期 reason=dead_loop, 实际 {result.reason}"
    assert len(result.objects) == 0


async def test_call_diversity_passes():
    """不同工具调用不被误判 dead_loop — 正常完成."""
    call1 = _MockToolCall(id="c1", name="list_dir", input={"path": "."})
    call2 = _MockToolCall(id="c2", name="read_file", input={"path": "pom.xml"})
    call3 = _MockToolCall(id="c3", name="read_file", input={"path": "src/main/java/Order.java"})
    end_call = _MockToolUseResponse(text="done", tool_calls=[])

    mock_llm = AsyncMock()
    mock_llm.side_effect = [
        _MockToolUseResponse(text="", tool_calls=[call1]),
        _MockToolUseResponse(text="", tool_calls=[call2]),
        _MockToolUseResponse(text="", tool_calls=[call3]),
        end_call,
    ]
    with patch("app.knowledge.extraction_agent.chat_completion_with_tools", mock_llm):
        result = await run_extraction_agent(repo_path="/tmp/fake", hint_text=None, max_iterations=10)

    assert result.status == "ok"
    assert result.reason != "dead_loop"
