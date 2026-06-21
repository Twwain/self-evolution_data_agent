"""Integration: orchestrated extraction ≈ single-agent extraction (golden fixture)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio

# Resolve fixture path relative to this test file -- same pattern as
# test_agentic_golden_schema.py:13.  pytest runs from backend/ directory,
# but the path MUST be absolute (not cwd-relative) to survive chdir.
_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "agentic_golden"
_MANIFEST = json.loads((_FIXTURE_DIR / "golden_manifest.json").read_text())
_GOLDEN_SCHEMA = json.loads((_FIXTURE_DIR / "golden_schema.json").read_text())

# Recall threshold for the Explorer (acceptance Gate, 02-acceptance.md:65).
_RECALL_MIN = 0.90


@pytest.mark.integration
@pytest.mark.llm
async def test_orchestrated_produces_all_golden_entities():
    """Orchestrated extraction yields all expected entities from golden fixture.

    Address is an ``@Embeddable`` embedded in Order via
    ``@Embedded private Address shippingAddress``. Per the project's flatten
    contract (golden_schema.json), it is NOT a standalone object: its columns
    (street/city/zip_code) flatten directly into ``orders``, and the outer
    ``shippingAddress`` property is not a DB column. So we verify Address by its
    flattened columns living on ``orders`` — not by a separate "address" object.
    """
    from app.knowledge.skeleton.orchestrator import orchestrated_extraction

    result = await orchestrated_extraction(
        repo_path=str(_FIXTURE_DIR), repo_name="golden"
    )
    objects = {o["name"]: o for o in result.objects}
    assert "orders" in objects, f"missing orders, got {set(objects)}"
    assert "customers" in objects, f"missing customers, got {set(objects)}"

    # @Embedded Address flattened into orders — assert its columns are present.
    order_field_names = {
        f["name"] if isinstance(f, dict) else f
        for f in objects["orders"].get("fields", [])
    }
    flattened = {"street", "city", "zip_code"}
    assert flattened & order_field_names, (
        "embedded Address columns missing from orders; "
        f"got order fields {order_field_names}"
    )


@pytest.mark.integration
@pytest.mark.llm
async def test_orchestrated_status_ok_for_small_repo():
    """Orchestrated extraction returns ok status for a tiny repository."""
    from app.knowledge.skeleton.orchestrator import orchestrated_extraction

    result = await orchestrated_extraction(
        repo_path=str(_FIXTURE_DIR), repo_name="golden"
    )
    assert result.status == "ok", f"got {result.status}: {result.reason}"
    assert len(result.objects) >= 1


@pytest.mark.integration
@pytest.mark.llm
async def test_orchestrated_equivalent_to_single_agent():
    """Orchestrated (multi-subagent) extraction covers all entities found by single-agent."""
    from app.knowledge.extraction_agent import run_extraction_agent
    from app.knowledge.skeleton.orchestrator import orchestrated_extraction

    single, orch = await asyncio.gather(
        run_extraction_agent(repo_path=str(_FIXTURE_DIR), repo_name="single"),
        orchestrated_extraction(repo_path=str(_FIXTURE_DIR), repo_name="orch"),
    )
    s_names = {o["name"] for o in single.objects}
    o_names = {o["name"] for o in orch.objects}
    missing = s_names - o_names
    assert not missing, f"orchestrated missed: {missing}"
    extra = o_names - s_names
    if extra:
        print(f"Orchestrated found extra: {extra} (fresh subagent contexts)")


# ── Recall gates vs golden_manifest.json (acceptance #36 / #37) ──────────────


@pytest.mark.integration
@pytest.mark.llm
async def test_explorer_golden_recall():
    """#36: Explorer focus_classes recall vs golden_manifest entities ≥ 90%.

    Entities in golden_manifest are class names (Order/Customer/Address/
    OrderStatus). The Explorer reasons over the tree-sitter class index, so its
    focus_classes are class names too — a direct name-to-name comparison. Guards
    against the Explorer silently missing an entity before split/fan-out.
    """
    from app.knowledge.skeleton.explorer import explore_repo
    from app.knowledge.skeleton.scanner import scan_skeleton

    skeleton = scan_skeleton(str(_FIXTURE_DIR))
    result = await explore_repo(
        repo_path=str(_FIXTURE_DIR), skeleton=skeleton, repo_name="golden"
    )
    expected = set(_MANIFEST["entities"])
    found = set(result.focus_classes)
    recalled = expected & found
    recall = len(recalled) / len(expected)
    assert recall >= _RECALL_MIN, (
        f"Explorer recall {recall:.0%} < {_RECALL_MIN:.0%}; "
        f"missed {expected - found}, got {found}"
    )
    # OrderRepository is a JPA repository interface — must NOT be a focus class.
    assert "OrderRepository" not in found, "JPA repository interface leaked into focus_classes"


@pytest.mark.integration
@pytest.mark.llm
async def test_orchestrated_recall_vs_golden_manifest():
    """#37: every golden_manifest entity is evidenced in the orchestrated output.

    Ground truth is class names; the extracted output is DB-shaped per the
    project's flatten contract (golden_schema.json), so a naive
    ``entities ⊆ object_names`` check is wrong — Address is @Embeddable
    (flattened into orders, no standalone object) and OrderStatus is an enum
    (surfaced as enum_values, not an object). Each manifest entity is verified
    against its contract-appropriate evidence; this is what would have caught
    the split_file_list Address-drop defect end to end.
    """
    from app.knowledge.skeleton.orchestrator import orchestrated_extraction

    result = await orchestrated_extraction(
        repo_path=str(_FIXTURE_DIR), repo_name="golden"
    )
    object_names = {o["name"].lower() for o in result.objects}
    enum_classes = {c.lower() for c in _GOLDEN_SCHEMA.get("enum_classes", [])}
    # Files cited by any field's source_ref — evidence that an @Embeddable's
    # columns were flattened in from its source file.
    cited_files = {
        f["source_ref"].split(":")[0]
        for o in result.objects
        for f in o.get("fields", [])
        if isinstance(f, dict) and f.get("source_ref")
    }
    # Any field carrying enum_values — evidence an enum class was consumed.
    has_enum_values = any(
        isinstance(f, dict) and f.get("enum_values")
        for o in result.objects
        for f in o.get("fields", [])
    )

    missing = []
    for entity in _MANIFEST["entities"]:
        el = entity.lower()
        if el in object_names or f"{el}s" in object_names:
            continue  # standalone object (Order→orders, Customer→customers)
        if el in enum_classes:
            assert has_enum_values, f"enum entity '{entity}' produced no enum_values"
            continue
        if any(f.endswith(f"{entity}.java") for f in cited_files):
            continue  # @Embeddable flattened in — source file cited
        missing.append(entity)

    assert not missing, (
        f"manifest entities not evidenced in output: {missing}; "
        f"objects={object_names}, cited_files={cited_files}"
    )
