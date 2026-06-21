"""Tests for skeleton scanner (Task 0: merge_results + Task 2: scanner)."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.knowledge.extraction_agent import ExtractionResult
from app.knowledge.skeleton._base import ExplorerResult, SubagentResult, WorkUnit, merge_results


def test_merge_results_preserves_reasons():
    sr1 = SubagentResult(work_unit_name="a",
        result=ExtractionResult(objects=[{"name": "a1"}], knowledge_proposals=[], status="ok"))
    sr2 = SubagentResult(work_unit_name="b",
        result=ExtractionResult(objects=[], knowledge_proposals=[],
                                status="partial", reason="iteration_cap"))
    merged = merge_results([sr1, sr2])
    assert merged.status == "partial"
    assert "b: iteration_cap" in merged.reason
    assert len(merged.objects) == 1


def test_merge_results_one_null_one_ok():
    sr1 = SubagentResult(work_unit_name="a",
        result=ExtractionResult(objects=[{"name": "a1"}], knowledge_proposals=[], status="ok"))
    sr2 = SubagentResult(work_unit_name="b", result=None)  # crashed
    merged = merge_results([sr1, sr2])
    assert merged.status == "partial"
    assert "b: exception" in merged.reason
    assert len(merged.objects) == 1


def test_merge_results_all_null_returns_failed():
    sr1 = SubagentResult(work_unit_name="a", result=None)
    sr2 = SubagentResult(work_unit_name="b", result=None)
    merged = merge_results([sr1, sr2])
    assert merged.status == "failed"
    assert merged.objects == []


def test_merge_results_all_ok():
    sr1 = SubagentResult(work_unit_name="a",
        result=ExtractionResult(objects=[{"name": "a1"}], knowledge_proposals=[], status="ok"))
    sr2 = SubagentResult(work_unit_name="b",
        result=ExtractionResult(objects=[{"name": "b1"}], knowledge_proposals=[], status="ok"))
    merged = merge_results([sr1, sr2])
    assert merged.status == "ok"
    assert len(merged.objects) == 2


# ── Scanner tests ─────────────────────────────────────────────────


@pytest.fixture
def multi_module_java_repo():
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="skeleton_test_")
    base = Path(tmp)
    order_dir = base / "src/main/java/com/x/order"
    order_dir.mkdir(parents=True)
    (order_dir / "Order.java").write_text(
        '@Entity @Table(name="orders") public class Order { private Long id; }')
    (order_dir / "OrderItem.java").write_text(
        '@Entity @Table(name="order_items") public class OrderItem { private Long id; }')
    (order_dir / "OrderStatus.java").write_text(
        'public enum OrderStatus { PENDING, CONFIRMED }')
    prod_dir = base / "src/main/java/com/x/product"
    prod_dir.mkdir(parents=True)
    (prod_dir / "Product.java").write_text(
        '@Entity @Table(name="products") public class Product { private Long id; }')
    yield str(base)
    shutil.rmtree(tmp)


def test_scan_skeleton_discovers_all_classes(multi_module_java_repo):
    from app.knowledge.skeleton.scanner import scan_skeleton
    sk = scan_skeleton(multi_module_java_repo)
    for name in ("Order", "OrderItem", "OrderStatus", "Product"):
        assert name in sk.class_index, f"missing {name}"
        assert "java" in sk.class_index[name]


def test_scan_skeleton_class_index_relative_paths(multi_module_java_repo):
    from app.knowledge.skeleton.scanner import scan_skeleton
    sk = scan_skeleton(multi_module_java_repo)
    for p in sk.class_index.values():
        assert not Path(p).is_absolute()


def test_scan_empty_repo():
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="skeleton_empty_")
    try:
        from app.knowledge.skeleton.scanner import scan_skeleton
        sk = scan_skeleton(tmp)
        assert sk.class_index == {}
        assert sk.modules == []
    finally:
        shutil.rmtree(tmp)


def test_scan_skips_non_code_files(multi_module_java_repo):
    from app.knowledge.skeleton.scanner import scan_skeleton
    base = Path(multi_module_java_repo)
    (base / "pom.xml").write_text("<project></project>")
    sk = scan_skeleton(multi_module_java_repo)
    assert "project" not in sk.class_index
    assert "Order" in sk.class_index


def test_scan_module_has_classes_list(multi_module_java_repo):
    from app.knowledge.skeleton.scanner import scan_skeleton
    sk = scan_skeleton(multi_module_java_repo)
    all_cls = set()
    for m in sk.modules:
        all_cls.update(m.classes)
    assert "Order" in all_cls
    assert "Product" in all_cls


def test_grammar_not_installed_not_in_language_configs():
    """Nonexistent grammar must not appear in LANGUAGE_CONFIGS."""
    import app.knowledge.skeleton._base as base_module
    assert "nonexistent_lang_xyz" not in base_module.LANGUAGE_CONFIGS


def test_graceful_degradation_on_parse_error(tmp_path):
    """Corrupt source file → skip, other classes still found."""
    from app.knowledge.skeleton.scanner import scan_skeleton
    (tmp_path / "Good.java").write_text("public class Good {}")
    (tmp_path / "Bad.java").write_text("\x00\x00\x00")  # corrupt
    sk = scan_skeleton(str(tmp_path))
    assert "Good" in sk.class_index
    # Bad.java is skipped, not a crash


# ── Orchestrator tests ──────────────────────────────────────────────

# 两个 WorkUnit 供 split_file_list mock 共用
_TWO_UNITS = [
    WorkUnit(name="a", focus_classes=["Order"], focus_files=["a.java"]),
    WorkUnit(name="b", focus_classes=["Product"], focus_files=["b.java"]),
]

# Explorer 返回非空结果 (触发 split 路径)
_EXPLORER_OK = ExplorerResult(
    focus_files=["a.java", "b.java"],
    focus_classes=["Order", "Product"],
    reasoning="identified persistence entities",
    status="ok",
)


@pytest.mark.asyncio
@patch("app.knowledge.skeleton.orchestrator.split_file_list", return_value=_TWO_UNITS)
@patch("app.knowledge.skeleton.orchestrator.explore_repo", new_callable=AsyncMock)
@patch("app.knowledge.skeleton.orchestrator.scan_skeleton")
@patch("app.knowledge.skeleton.orchestrator.run_extraction_agent", new_callable=AsyncMock)
async def test_orchestrator_calls_subagent_per_work_unit(
    mock_run, mock_scan, mock_explore, mock_split
):
    from app.knowledge.skeleton._base import Module, Skeleton
    from app.knowledge.skeleton.orchestrator import orchestrated_extraction

    mock_scan.return_value = Skeleton(
        modules=[Module(name="a", files=["a.java"], classes=["Order"]),
                 Module(name="b", files=["b.java"], classes=["Product"])],
        class_index={"Order": "a.java", "Product": "b.java"})
    mock_explore.return_value = _EXPLORER_OK
    mock_run.side_effect = [
        ExtractionResult(objects=[{"name": "orders"}], knowledge_proposals=[], status="ok"),
        ExtractionResult(objects=[{"name": "products"}], knowledge_proposals=[], status="ok"),
    ]
    result = await orchestrated_extraction(repo_path="/tmp/x", repo_name="t")
    assert mock_run.call_count == 2
    assert len(result.objects) == 2
    assert result.status == "ok"


@pytest.mark.asyncio
@patch("app.knowledge.skeleton.orchestrator.split_file_list", return_value=_TWO_UNITS)
@patch("app.knowledge.skeleton.orchestrator.explore_repo", new_callable=AsyncMock)
@patch("app.knowledge.skeleton.orchestrator.scan_skeleton")
@patch("app.knowledge.skeleton.orchestrator.run_extraction_agent", new_callable=AsyncMock)
async def test_orchestrator_partial_on_any_subagent_partial(
    mock_run, mock_scan, mock_explore, mock_split
):
    from app.knowledge.skeleton._base import Module, Skeleton
    from app.knowledge.skeleton.orchestrator import orchestrated_extraction

    mock_scan.return_value = Skeleton(
        modules=[Module(name="a", files=["a.java"], classes=["Order"]),
                 Module(name="b", files=["b.java"], classes=["Product"])],
        class_index={"Order": "a.java", "Product": "b.java"})
    mock_explore.return_value = _EXPLORER_OK
    mock_run.side_effect = [
        ExtractionResult(objects=[], knowledge_proposals=[], status="ok"),
        ExtractionResult(objects=[{"name": "b"}], knowledge_proposals=[], status="partial",
                         reason="ic"),
    ]
    r = await orchestrated_extraction(repo_path="/tmp/x", repo_name="t")
    assert r.status == "partial"
    assert "ic" in r.reason


@pytest.mark.asyncio
@patch("app.knowledge.skeleton.orchestrator.scan_skeleton", side_effect=Exception("boom"))
@patch("app.knowledge.skeleton.orchestrator.run_extraction_agent", new_callable=AsyncMock)
async def test_graceful_degradation_on_scan_failure(mock_run, mock_scan):
    from app.knowledge.skeleton.orchestrator import orchestrated_extraction

    mock_run.return_value = ExtractionResult(
        objects=[{"name": "fb"}], knowledge_proposals=[], status="ok")
    r = await orchestrated_extraction(repo_path="/tmp/x", repo_name="t")
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs.get("skeleton") is None
    assert r.objects[0]["name"] == "fb"


@pytest.mark.asyncio
@patch("app.knowledge.skeleton.orchestrator.split_file_list", return_value=_TWO_UNITS)
@patch("app.knowledge.skeleton.orchestrator.explore_repo", new_callable=AsyncMock)
@patch("app.knowledge.skeleton.orchestrator.scan_skeleton")
@patch("app.knowledge.skeleton.orchestrator.run_extraction_agent", new_callable=AsyncMock)
async def test_orchestrator_all_subagents_fail(
    mock_run, mock_scan, mock_explore, mock_split
):
    from app.knowledge.skeleton._base import Module, Skeleton
    from app.knowledge.skeleton.orchestrator import orchestrated_extraction

    mock_scan.return_value = Skeleton(
        modules=[Module(name="a", files=["a.java"], classes=["Order"]),
                 Module(name="b", files=["b.java"], classes=["Product"])],
        class_index={"Order": "a.java", "Product": "b.java"})
    mock_explore.return_value = _EXPLORER_OK
    mock_run.side_effect = [
        Exception("subagent crash"),
        Exception("subagent crash"),
    ]
    r = await orchestrated_extraction(repo_path="/tmp/x", repo_name="t")
    assert r.status in ("failed", "partial")
    assert r.reason  # reasons aggregated


@pytest.mark.asyncio
@patch("app.knowledge.skeleton.orchestrator.split_file_list", return_value=_TWO_UNITS)
@patch("app.knowledge.skeleton.orchestrator.explore_repo", new_callable=AsyncMock)
@patch("app.knowledge.skeleton.orchestrator.scan_skeleton")
@patch("app.knowledge.skeleton.orchestrator.run_extraction_agent", new_callable=AsyncMock)
async def test_orchestrator_one_ok_one_crash(
    mock_run, mock_scan, mock_explore, mock_split
):
    from app.knowledge.extraction_agent import ExtractionResult
    from app.knowledge.skeleton._base import Module, Skeleton
    from app.knowledge.skeleton.orchestrator import orchestrated_extraction

    mock_scan.return_value = Skeleton(
        modules=[Module(name="a", files=["a.java"], classes=["Order"]),
                 Module(name="b", files=["b.java"], classes=["Product"])],
        class_index={"Order": "a.java", "Product": "b.java"})
    mock_explore.return_value = _EXPLORER_OK
    mock_run.side_effect = [
        ExtractionResult(objects=[{"name": "a"}], knowledge_proposals=[], status="ok"),
        Exception("subagent crash"),
    ]
    r = await orchestrated_extraction(repo_path="/tmp/x", repo_name="t")
    assert r.status == "partial"  # one ok, one crash → partial
    assert len(r.objects) == 1
    assert "b: exception" in r.reason


# ── Language config guard tests ────────────────────────────────────

def test_language_config_no_interface_declaration():
    """Gate 1: no language config should include interface_declaration.

    Rationale: interface_declaration is a behavioral abstraction node,
    not a data entity marker. tree-sitter cannot distinguish @Entity
    from @Service, so we exclude interfaces entirely to avoid false
    positives that explode subagent count (see rev2 spec).
    """
    from app.knowledge.skeleton._base import LANGUAGE_CONFIGS

    for lang_name, cfg in LANGUAGE_CONFIGS.items():
        assert "interface_declaration" not in cfg.entity_node_types, (
            f"Language {lang_name} still includes interface_declaration. "
            f"Remove it per the 80% rule (behavioral → exclude)."
        )


# ── New orchestrator tests (Rev 2) ────────────────────────────────


@pytest.mark.asyncio
@patch("app.knowledge.skeleton.orchestrator.split_file_list")
@patch("app.knowledge.skeleton.orchestrator.explore_repo", new_callable=AsyncMock)
@patch("app.knowledge.skeleton.orchestrator.scan_skeleton")
@patch("app.knowledge.skeleton.orchestrator.run_extraction_agent", new_callable=AsyncMock)
async def test_orchestrator_invokes_explorer_before_split(
    mock_run, mock_scan, mock_explore, mock_split
):
    """Explorer 被 await, 且其输出作为 split_file_list 的第一参数 — 证明顺序依赖."""
    from app.knowledge.skeleton._base import Module, Skeleton
    from app.knowledge.skeleton.orchestrator import orchestrated_extraction

    mock_scan.return_value = Skeleton(
        modules=[Module(name="a", files=["a.java"], classes=["Order"])],
        class_index={"Order": "a.java"},
    )
    explorer_result = ExplorerResult(
        focus_files=["order/Order.java"],
        focus_classes=["Order"],
        reasoning="entity detected",
        status="ok",
    )
    mock_explore.return_value = explorer_result
    mock_split.return_value = _TWO_UNITS
    mock_run.side_effect = [
        ExtractionResult(objects=[{"name": "order"}], knowledge_proposals=[], status="ok"),
        ExtractionResult(objects=[{"name": "product"}], knowledge_proposals=[], status="ok"),
    ]

    await orchestrated_extraction(repo_path="/tmp/x", repo_name="t")

    # Explorer 被调用
    mock_explore.assert_awaited_once()

    # split_file_list 第一参数 = explorer 输出的 focus_files (数据依赖证明顺序)
    call_args = mock_split.call_args
    assert call_args.args[0] == explorer_result.focus_files, (
        "split_file_list 第一参数应是 explorer_result.focus_files"
    )


@pytest.mark.asyncio
@patch("app.knowledge.skeleton.orchestrator.split_file_list")
@patch("app.knowledge.skeleton.orchestrator.explore_repo", new_callable=AsyncMock)
@patch("app.knowledge.skeleton.orchestrator.scan_skeleton")
@patch("app.knowledge.skeleton.orchestrator.run_extraction_agent", new_callable=AsyncMock)
async def test_orchestrator_fallback_on_explorer_empty(
    mock_run, mock_scan, mock_explore, mock_split
):
    """Explorer 返回空 focus_files → 走单 agent (skeleton=None), split 不被调用."""
    from app.knowledge.skeleton._base import Module, Skeleton
    from app.knowledge.skeleton.orchestrator import orchestrated_extraction

    mock_scan.return_value = Skeleton(
        modules=[Module(name="a", files=["a.java"], classes=["Order"])],
        class_index={"Order": "a.java"},
    )
    mock_explore.return_value = ExplorerResult(
        focus_files=[],
        focus_classes=[],
        reasoning="none",
        status="partial",
    )
    mock_run.return_value = ExtractionResult(
        objects=[{"name": "fallback"}], knowledge_proposals=[], status="ok"
    )

    r = await orchestrated_extraction(repo_path="/tmp/x", repo_name="t")

    # 单 agent 调用, skeleton=None
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs.get("skeleton") is None
    # split 未被调用
    mock_split.assert_not_called()
    assert r.objects[0]["name"] == "fallback"
