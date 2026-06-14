"""Stage 4 Task 9 — tool registry tests."""
from app.config import settings
from app.engine.tools.registry import REGISTRY, TOOL_SPECS, build_system_prompt
from app.models.namespace import Namespace


def test_registry_has_expected_tools():  # 原 test_registry_has_all_13_tools (名字里的 13 一直是错的)
    expected = {
        "lookup_knowledge", "save_knowledge",
        "fetch_schema", "inspect_values",
        "estimate_cost", "execute_query",
        "clarify_with_user",
        "generate_query_plan", "execute_plan", "present_result",
        "list_databases", "list_tables",
    }
    assert set(REGISTRY.keys()) == expected
    assert {s["name"] for s in TOOL_SPECS} == expected


def test_registry_callables_match_specs():
    """每个 spec name 在 REGISTRY 都有对应 callable."""
    for spec in TOOL_SPECS:
        assert spec["name"] in REGISTRY
        assert callable(REGISTRY[spec["name"]])


def test_each_tool_spec_well_formed():
    for spec in TOOL_SPECS:
        assert "name" in spec
        assert "description" in spec
        assert "input_schema" in spec
        schema = spec["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        # required 字段可选 (有些 tool 全 optional 不要求 required)


def test_system_prompt_substitutes_config_values():
    ns = Namespace(slug="t9_ns", name="t9")
    prompt = build_system_prompt(settings=settings, namespace=ns)
    # 验证 placeholders 被替换
    assert "{single_layer_limit" not in prompt
    assert "{total_limit" not in prompt
    # namespace 由 dispatcher 注入到工具调用, LLM 不感知, prompt 内不含 ns_slug
    assert "{ns_slug" not in prompt
    # 验证 config 值出现 (可能带 , 千位分隔)
    # query_cost_single_layer_limit=50000 → "50,000" 被 :, format 渲染
    assert f"{settings.query_cost_single_layer_limit:,}" in prompt
    # 2026-05-12 reform: 不再向 LLM 暴露迭代配额数字
    assert "迭代上限" not in prompt


def test_input_schema_has_no_runtime_context_kwargs():
    """Tool specs should NOT expose db / namespace_id / ns_slug / datasource_id
    to LLM (those are runtime-injected by dispatcher)."""
    forbidden = {"db", "namespace_id", "ns_slug", "datasource_id"}
    for spec in TOOL_SPECS:
        props = spec["input_schema"].get("properties", {})
        leaked = set(props.keys()) & forbidden
        assert not leaked, f"tool {spec['name']} leaks runtime kwargs: {leaked}"


def test_system_prompt_mentions_catalog_tools():
    """冷启动方法论: prompt 须引导用 list_databases 自主探索."""
    from app.config import settings
    from app.engine.tools.registry import build_system_prompt
    from app.models.namespace import Namespace
    ns = Namespace(slug="t_cat", name="t_cat")
    prompt = build_system_prompt(settings=settings, namespace=ns)
    assert "list_databases" in prompt
    assert "list_tables" in prompt
    # 语义路由方法论关键词
    assert "语义" in prompt
    # D7 escape clause 存在
    assert "clarify_with_user" in prompt
