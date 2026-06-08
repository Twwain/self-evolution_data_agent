"""Phase 2 Task 4: 动态 SQL 全分支枚举."""
import pytest

from app.config import settings
from app.knowledge.dynamic_branch import (
    DynamicSQLBranch,
    enumerate_dynamic_branches,
    select_representative_branches,
)


def test_two_ifs_yield_four_branches():
    """两个 <if> 应展开 2^2 = 4 个分支."""
    select_xml = """
    <select id="selectFiltered">
        SELECT * FROM t_order WHERE 1=1
        <if test="userId != null">AND user_id = #{userId}</if>
        <if test="status != null">AND status = #{status}</if>
    </select>
    """
    branches = enumerate_dynamic_branches(select_xml, method_id="selectFiltered")

    assert len(branches) == 4
    sqls = {b.sql for b in branches}
    # Should have a branch with both conditions
    assert any("user_id" in s and "status" in s for s in sqls)
    # Should have a branch with neither condition
    assert any("user_id" not in s and "status" not in s for s in sqls)


def test_choose_yields_n_plus_one_branches():
    """<choose><when×2/><otherwise/></choose> 产 3 个分支."""
    select_xml = """
    <select id="selectByMode">
        SELECT * FROM t_order
        <choose>
            <when test="mode == 'A'">WHERE a=1</when>
            <when test="mode == 'B'">WHERE b=1</when>
            <otherwise>WHERE 1=1</otherwise>
        </choose>
    </select>
    """
    branches = enumerate_dynamic_branches(select_xml, method_id="selectByMode")
    assert len(branches) == 3


def test_cap_at_max_branches(monkeypatch):
    """超 dynamic_sql_max_branches 时截断."""
    monkeypatch.setattr(settings, "dynamic_sql_max_branches", 4)
    # 5 个 if → 2^5=32 → 截到 4
    ifs = "".join(f'<if test="c{i}">AND c{i}=1</if>' for i in range(5))
    select_xml = f"<select id='m'>SELECT * FROM t WHERE 1=1 {ifs}</select>"
    branches = enumerate_dynamic_branches(select_xml, method_id="m")
    assert len(branches) <= 4


def test_no_dynamic_tags_yields_single_branch():
    """无动态标签时返回单个分支."""
    select_xml = """
    <select id="selectAll">
        SELECT * FROM t_order WHERE status = 1
    </select>
    """
    branches = enumerate_dynamic_branches(select_xml, method_id="selectAll")
    assert len(branches) == 1
    assert "status" in branches[0].sql


@pytest.mark.asyncio
async def test_select_representative_uses_llm(fake_llm):
    """超上限时 LLM 选 N 个最具业务意义."""
    fake_llm.queue_response({
        "selected_indices": [0, 5, 10, 15],
    })
    branches = [
        DynamicSQLBranch(sql=f"SELECT {i}", branch_conditions=[f"c{i}"], nl_hint=f"hint{i}")
        for i in range(20)
    ]
    selected = await select_representative_branches(
        branches, method_id="m", target_count=4,
    )
    assert len(selected) == 4
    assert selected[0].sql == "SELECT 0"
