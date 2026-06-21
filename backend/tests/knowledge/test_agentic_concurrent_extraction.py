"""并发 run_extraction_agent — 两次调用互不干扰 (共享状态隔离)."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.knowledge.extraction_agent import run_extraction_agent

pytestmark = pytest.mark.asyncio


async def test_concurrent_runs_isolated():
    mock_llm = AsyncMock(side_effect=RuntimeError("immediate fail"))
    with patch("app.knowledge.extraction_agent.chat_completion_with_tools", mock_llm):
        r1, r2 = await asyncio.gather(
            run_extraction_agent(repo_path="/tmp/a", max_iterations=1),
            run_extraction_agent(repo_path="/tmp/b", max_iterations=1),
        )
    assert r1.status == "failed"
    assert r2.status == "failed"
    assert mock_llm.call_count == 2
