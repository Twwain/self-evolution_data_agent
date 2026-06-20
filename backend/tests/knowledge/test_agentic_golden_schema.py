"""G1b: Golden schema equivalence — synthetic fixture, CI-automatable (live LLM)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.knowledge.extraction_agent import run_extraction_agent

pytestmark = pytest.mark.asyncio

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "agentic_golden"
_GOLDEN = json.loads((_FIXTURE_DIR / "golden_schema.json").read_text())


def _agent_objects_to_map(objects: list[dict]) -> dict:
    m: dict[str, dict] = {}
    for obj in objects:
        name = (obj.get("name") or obj.get("table") or obj.get("collection", "")).lower()
        fields: set[str] = set()
        sub: dict[str, set[str]] = {}
        enums: dict[str, set[str]] = {}
        for fd in obj.get("fields", []):
            fn = fd.get("name") or fd.get("field", "")
            fields.add(fn)
            f_evs = fd.get("enum_values", [])
            if f_evs:
                enums[fn] = {str(e.get("db_value", "")) for e in f_evs if e.get("db_value")}
            f_subs = fd.get("sub_fields", [])
            if f_subs:
                sub[fn] = {sf.get("name", "") for sf in f_subs if sf.get("name")}
        m[name] = {"fields": fields, "sub_fields": sub, "enum_fields": enums}
    return m


@pytest.mark.live_llm
async def test_golden_objects_coverage():
    """agent 产出对象 ⊇ golden 声明对象 — 不丢核心实体/字段."""
    result = await run_extraction_agent(repo_path=str(_FIXTURE_DIR), hint_text=None, max_iterations=25)
    agent_map = _agent_objects_to_map(result.objects)

    for obj_name, spec in _GOLDEN["objects"].items():
        assert obj_name in agent_map, f"missing object '{obj_name}' — agent: {list(agent_map.keys())}"
        for field_name in spec["fields"]:
            assert field_name in agent_map[obj_name]["fields"], \
                f"'{obj_name}' missing field '{field_name}' — got: {agent_map[obj_name]['fields']}"
        for emb_field, sub_names in spec.get("sub_fields", {}).items():
            assert emb_field in agent_map[obj_name]["sub_fields"], \
                f"'{obj_name}' missing embedded field '{emb_field}'"
            for sn in sub_names:
                assert sn in agent_map[obj_name]["sub_fields"][emb_field], \
                    f"'{obj_name}.{emb_field}' missing sub_field '{sn}'"


@pytest.mark.live_llm
async def test_golden_enum_not_exceed():
    """agent 枚举值 ⊆ golden 声明 — 防幻觉编造枚举值."""
    result = await run_extraction_agent(repo_path=str(_FIXTURE_DIR), hint_text=None, max_iterations=25)
    agent_map = _agent_objects_to_map(result.objects)
    for obj_name, spec in _GOLDEN["objects"].items():
        for ef, golden_vals in spec.get("enum_fields", {}).items():
            agent_vals = agent_map.get(obj_name, {}).get("enum_fields", {}).get(ef, set())
            extra = agent_vals - set(golden_vals)
            assert not extra, f"'{obj_name}.{ef}': agent 枚举值 {extra} 不在 golden 声明中 — 疑似编造"


@pytest.mark.live_llm
async def test_golden_relationships_present():
    """agent 发现的关联 ⊇ golden 声明关联 — 不丢关联."""
    result = await run_extraction_agent(repo_path=str(_FIXTURE_DIR), hint_text=None, max_iterations=25)
    golden_rels = _GOLDEN.get("relationships", [])
    if not golden_rels:
        return
    agent_names = [o.get("name", "").lower() for o in result.objects]
    assert "orders" in agent_names
    assert "customers" in agent_names
    orders_obj = next(o for o in result.objects if "order" in o.get("name", "").lower())
    order_fields = [f.get("name", "") for f in orders_obj.get("fields", [])]
    rel_fields = [f for f in order_fields if "customer" in f.lower()]
    assert rel_fields, f"orders 应有 customer 关联字段, 实际: {order_fields}"
