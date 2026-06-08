"""Stage 4 Task 8 — plan_tools tests.

Mock plan_generator / plan_executor / visualizer to avoid LLM cost.
真实多步 Plan 执行已由 test_plan_executor.py 覆盖.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ════════════════════════════════════════════
#  generate_query_plan
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_generate_query_plan_passes_collections_and_calls_generator():
    """验证新契约: collections/filters/schemas 透传给 generate_plan."""
    fake_plan = MagicMock()
    fake_plan.strategy = "single_aggregate"
    fake_plan.steps = []
    fake_plan.databases = ["test_db"]
    fake_plan.to_dict = MagicMock(return_value={
        "strategy": "single_aggregate", "steps": [], "post_process": "",
    })

    with patch(
        "app.engine.tools.plan_tools.generate_plan",
        new=AsyncMock(return_value=fake_plan),
    ) as mock_gen, patch(
        "app.engine.tools.plan_tools.resolve_ds",
        new=AsyncMock(return_value=None),
    ):
        from app.engine.tools.plan_tools import generate_query_plan

        out = await generate_query_plan(
            db=MagicMock(),
            question="订单数量",
            namespace_id=1,
            collections=[{
                "db_type": "mongodb",
                "database": "test_db",
                "collection": "c_product",
            }],
            filters=[{
                "collection": "c_product",
                "field": "standard",
                "op": "regex",
                "value": "优选",
            }],
            schemas={"c_product": {"key_fields": ["_id", "standard"]}},
        )

        assert "plan" in out
        assert out["plan"]["strategy"] == "single_aggregate"
        # generate_plan(question, collections, filters, schemas, knowledge, rules)
        call_args = mock_gen.call_args
        assert call_args[0][0] == "订单数量"
        assert call_args[0][1][0]["collection"] == "c_product"
        assert call_args[0][1][0]["db_type"] == "mongodb"
        assert call_args[0][2][0]["field"] == "standard"


@pytest.mark.asyncio
async def test_generate_query_plan_trace_shape_no_keyerror():
    """回归: 用线上 trace 真实形状 (db_type/database/collection + dict schemas) 不再 KeyError.

    历史 bug: _dict_to_target 硬取 d["business_term"] → KeyError. 新契约直接透传.
    """
    fake_plan = MagicMock()
    fake_plan.to_dict = MagicMock(return_value={
        "strategy": "multi_step", "steps": [], "post_process": "",
    })
    with patch(
        "app.engine.tools.plan_tools.generate_plan",
        new=AsyncMock(return_value=fake_plan),
    ), patch(
        "app.engine.tools.plan_tools.resolve_ds",
        new=AsyncMock(return_value=None),
    ):
        from app.engine.tools.plan_tools import generate_query_plan

        out = await generate_query_plan(
            db=MagicMock(),
            question="统计品牌各资源类型数量和占比",
            namespace_id=1,
            collections=[
                {"db_type": "mongodb", "database": "rp_db", "collection": "c_brand"},
                {"db_type": "mongodb", "database": "rp_db", "collection": "c_module"},
            ],
            filters=[
                {"collection": "c_brand", "field": "brandName", "op": "regex", "value": "A 级|B 级"},
            ],
            schemas={
                "c_brand": {"key_fields": ["id", "brandName"]},
                "c_module": {"key_fields": ["docId", "groups.resources.itemType"]},
            },
        )
        assert out["plan"]["strategy"] == "multi_step"


# ════════════════════════════════════════════
#  execute_plan_tool
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_plan_returns_rows_and_columns():
    """验证 dict→QueryPlan 重建 + execute_plan 调用 + 结果展平."""
    fake_result = MagicMock()
    fake_result.final = [{"_id": "x"}]
    fake_result.step_results = {1: [{"_id": "x"}]}
    fake_result.last_step_idx = 1

    with patch(
        "app.engine.tools.plan_tools.execute_plan",
        new=AsyncMock(return_value=fake_result),
    ) as mock_exec, patch(
        "app.engine.tools.plan_tools._dict_to_query_plan",
        return_value=MagicMock(),
    ):
        from app.engine.tools.plan_tools import execute_plan_tool

        out = await execute_plan_tool(
            namespace_id=1, ns_slug="test_ns",
            plan={
                "strategy": "single_aggregate",
                "steps": [],
                "post_process": "",
            },
        )
        assert "rows" in out
        assert out["rows"] == [{"_id": "x"}]
        assert out["columns"] == ["_id"]
        mock_exec.assert_awaited_once()


# ════════════════════════════════════════════
#  recommend_chart_tool
# ════════════════════════════════════════════

def test_recommend_chart_card_for_single_value():
    """单行单值 → card / 类似. 不强断具体类型, 只验合约."""
    from app.engine.tools.plan_tools import recommend_chart_tool
    out = recommend_chart_tool(rows=[{"count": 42}], columns=["count"])
    assert "chart_type" in out
    assert "config" in out
    assert isinstance(out["chart_type"], str)
    assert isinstance(out["config"], dict)


def test_recommend_chart_handles_empty_rows():
    """空结果不应崩溃, 落 table."""
    from app.engine.tools.plan_tools import recommend_chart_tool
    out = recommend_chart_tool(rows=[], columns=[])
    assert "chart_type" in out
    assert out["chart_type"] == "table"


def test_recommend_chart_multi_category_falls_back_to_table():
    """≥2 个分类维度 (brand × itemType) → 2D 图无法无损表达, 落 table.

    回归锁定: trace 7270955a — LLM 传 category_column='brandName' 时仍须落表,
    守卫必须置于 category_column 分支之前.
    """
    from app.engine.tools.plan_tools import recommend_chart_tool
    rows = [
        {"brandName": "优选 A 级标准版", "itemTypeName": "条目", "resourceCount": 1019},
        {"brandName": "优选 A 级标准版", "itemTypeName": "短文", "resourceCount": 28},
        {"brandName": "优选 B 级标准版", "itemTypeName": "条目", "resourceCount": 500},
    ]
    out = recommend_chart_tool(
        rows=rows,
        columns=["brandName", "itemTypeName", "resourceCount"],
        category_column="brandName",
    )
    assert out["chart_type"] == "table"


def test_recommend_chart_single_category_still_charts():
    """单分类维度 + 数值 (name × count) 不应被守卫误伤, 仍出图 (非 table/card)."""
    from app.engine.tools.plan_tools import recommend_chart_tool
    rows = [{"name": f"c{i}", "count": i} for i in range(7)]
    out = recommend_chart_tool(rows=rows, columns=["name", "count"])
    assert out["chart_type"] not in ("table", "card")
