"""Tests for skeleton context injection — Rev 2 free-exploration design."""
from app.knowledge.skeleton._base import WorkUnit


def test_format_skeleton_context_includes_classes():
    """Test 12 (updated): Rev 2 WorkUnit renders Focus Entities + Repository Index correctly."""
    from app.knowledge.extraction_agent import _format_skeleton_context

    wu = WorkUnit(
        name="com/x/order",
        focus_files=["src/x/Order.java", "src/x/OrderItem.java"],
        focus_classes=["Order", "OrderItem"],
        skeleton_class_index={
            "Order": "src/x/Order.java",
            "OrderItem": "src/x/OrderItem.java",
            "Product": "src/y/Product.java",
        },
    )
    ctx = _format_skeleton_context(wu)

    assert "Focus Entities" in ctx
    assert "Order.java" in ctx
    assert "Order" in ctx
    assert "OrderItem" in ctx
    assert "Repository Index" in ctx
    assert "Product" in ctx  # cross-index entry


def test_format_skeleton_context_has_guidance():
    """Test 13 (updated): Rev 2 context includes Guidance section with free-exploration text."""
    from app.knowledge.extraction_agent import _format_skeleton_context

    wu = WorkUnit(
        name="x",
        focus_files=["src/x/Order.java"],
        focus_classes=["Order"],
        skeleton_class_index={"Order": "src/x/Order.java"},
    )
    ctx = _format_skeleton_context(wu)

    assert "Focus Entities" in ctx
    assert "Guidance" in ctx
    assert "Explore freely" in ctx


def test_subagent_has_full_repo_access():
    """Test 29: Rev 2 prompt grants free exploration and has no sandbox restriction."""
    from app.knowledge.extraction_agent import _format_skeleton_context

    wu = WorkUnit(
        name="com/x/order",
        focus_files=["src/x/Order.java"],
        focus_classes=["Order"],
        skeleton_class_index={"Order": "src/x/Order.java"},
    )
    ctx = _format_skeleton_context(wu)

    # Rev 2 must grant free exploration
    assert "Explore freely" in ctx
    # Rev 2 must NOT contain old sandbox restriction
    assert "do not explore unrelated modules" not in ctx
    # Guidance must explicitly mention discovering entities not in the focus list
    assert "extract them too" in ctx


def test_format_skeleton_context_new_fields_take_priority():
    """Design §2.3 priority test: new fields win when both new and old fields are set."""
    from app.knowledge.extraction_agent import _format_skeleton_context

    wu = WorkUnit(
        name="mixed",
        # Rev 2 fields
        focus_files=["src/x/Order.java"],
        focus_classes=["Order"],
        skeleton_class_index={"Order": "src/x/Order.java"},
        # Rev 1 deprecated fields also set
        scope_dir="legacy/dir",
        class_index_subset={"LegacyClass": "legacy/L.java"},
        full_class_index={"LegacyClass": "legacy/L.java"},
    )
    ctx = _format_skeleton_context(wu)

    # New field content must appear
    assert "Focus Entities" in ctx
    assert "Order" in ctx
    # Old field values must NOT appear — new fields win
    assert "legacy/dir" not in ctx
    assert "Your Assignment" not in ctx


def test_format_skeleton_context_falls_back_to_old_fields():
    """Backward-compat: WorkUnit with only old fields still renders without error."""
    from app.knowledge.extraction_agent import _format_skeleton_context

    wu = WorkUnit(
        name="com/x/order",
        scope_dir="com/x/order",
        class_index_subset={"Order": "com/x/order/Order.java"},
        full_class_index={
            "Order": "com/x/order/Order.java",
            "Product": "com/y/product/Product.java",
        },
    )
    ctx = _format_skeleton_context(wu)

    # Old-field fallback renders without error and includes key content
    assert "Order" in ctx
