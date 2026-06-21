"""Agentic escape clause — empty repo must not fabricate objects (live LLM)."""
import pytest

from app.knowledge.extraction_agent import run_extraction_agent

pytestmark = pytest.mark.asyncio


@pytest.mark.live_llm
async def test_empty_repo_returns_zero_objects(tmp_path):
    """无持久化框架的空仓库 → agent 报告'未发现' → 0 objects."""
    repo = tmp_path / "empty_repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Empty Project\nNo code here.\n")

    result = await run_extraction_agent(repo_path=str(repo), hint_text=None, max_iterations=15)

    assert result.status in ("ok", "partial"), f"空仓库应正常终止, 实际 status={result.status}"
    assert len(result.objects) == 0, \
        f"空仓库不应编造任何对象, 实际 {len(result.objects)}: {[o.get('name','?') for o in result.objects]}"
