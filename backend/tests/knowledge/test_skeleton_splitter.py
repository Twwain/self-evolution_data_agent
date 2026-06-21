"""Tests for work unit splitter — 3-tier fallback."""
import pytest
from app.knowledge.skeleton._base import Module, Skeleton
from app.knowledge.skeleton.splitter import split_skeleton


def _mk_sk(modules_data: list[tuple[str, list[str], list[str]]]) -> Skeleton:
    modules = [Module(name=n, files=f, classes=c) for n, f, c in modules_data]
    ci = {}
    for m in modules:
        for cls in m.classes:
            ci[cls] = m.files[0] if m.files else f"{cls}.java"
    return Skeleton(modules=modules, class_index=ci)


def test_split_by_directory():
    sk = _mk_sk([("com/x/a", ["A.java"], ["A"]), ("com/x/b", ["B.java"], ["B"])])
    units = split_skeleton(sk, max_classes_per_unit=30)
    assert len(units) == 2
    assert {u.name for u in units} == {"com/x/a", "com/x/b"}


def test_split_by_file_when_dir_too_large():
    files = [f"E{i}.java" for i in range(4)]
    cls = [f"E{i}" for i in range(4)]
    sk = _mk_sk([("com/x/big", files, cls)])
    units = split_skeleton(sk, max_classes_per_unit=2)
    assert len(units) >= 2
    for u in units:
        assert len(u.class_index_subset) <= 2


def test_uniform_split_when_single_file_too_large():
    cls = [f"Entity{i}" for i in range(10)]
    sk = _mk_sk([("flat", ["models.py"], cls)])
    units = split_skeleton(sk, max_classes_per_unit=3)
    assert len(units) >= 4
    assert sum(len(u.class_index_subset) for u in units) == 10


def test_single_module_one_unit():
    sk = _mk_sk([("small", ["A.java"], ["A"])])
    units = split_skeleton(sk, max_classes_per_unit=30)
    assert len(units) == 1


def test_full_class_index_passed_to_every_unit():
    sk = _mk_sk([("a", ["A.java"], ["A"]), ("b", ["B.java"], ["B"])])
    sk.class_index["Ext"] = "lib/Ext.java"
    for u in split_skeleton(sk):
        assert "Ext" in u.full_class_index


def test_empty_skeleton():
    assert split_skeleton(Skeleton()) == []


# ── split_file_list unit tests (Rev 2) ───────────────────────────────────────

from app.knowledge.skeleton.splitter import split_file_list  # noqa: E402


def test_split_file_list_empty_input():
    """空类列表 + 空文件列表 → 返回空列表."""
    assert split_file_list([], [], {}) == []


def test_split_file_list_under_limit():
    """3 个类, max=10 → 1 个 WorkUnit 包含全部."""
    ci = {"Order": "order/Order.java", "Customer": "cust/Customer.java",
          "Product": "prod/Product.java"}
    units = split_file_list(list(ci.values()), list(ci.keys()), ci, max_files_per_unit=10)
    assert len(units) == 1
    assert set(units[0].focus_classes) == {"Order", "Customer", "Product"}


def test_split_file_list_over_limit_splits_uniformly():
    """10 个类, max=3 → 4 WorkUnit (3,3,3,1), 每 unit ≤3."""
    classes = [f"Entity{i}" for i in range(10)]
    ci = {c: f"{c}.java" for c in classes}
    units = split_file_list(list(ci.values()), classes, ci, max_files_per_unit=3)
    assert len(units) == 4
    sizes = [len(u.focus_classes) for u in units]
    assert sizes == [3, 3, 3, 1]
    for u in units:
        assert len(u.focus_classes) <= 3
    # 所有类恰好覆盖一次
    all_cls = [c for u in units for c in u.focus_classes]
    assert sorted(all_cls) == sorted(classes)


def test_split_file_list_none_default_falls_back_to_settings(monkeypatch):
    """max=None → 读 settings.agentic_extract_max_work_unit_size; 设为 1 → 3 units."""
    import app.knowledge.skeleton.splitter as splitter_mod
    monkeypatch.setattr(splitter_mod.settings, "agentic_extract_max_work_unit_size", 1)
    classes = ["Order", "Customer", "Product"]
    ci = {c: f"{c}.java" for c in classes}
    units = split_file_list(list(ci.values()), classes, ci)  # max_files_per_unit=None
    assert len(units) == 3
    for u in units:
        assert len(u.focus_classes) == 1


def test_split_file_list_golden_fanout():
    """4 通用电商实体, max=1 → 4 WorkUnit, 各含 1 类; 所有 .java 路径均覆盖."""
    classes = ["Order", "Customer", "Product", "Invoice"]
    ci = {c: f"entities/{c}.java" for c in classes}
    java_files = list(ci.values())  # .java 文件路径 (Explorer 实际传入形态)
    # files 只含 .java, 无额外文件 → extra_files 为空, 行为与修复前一致
    units = split_file_list(java_files, classes, ci, max_files_per_unit=1)
    assert len(units) == 4
    assert [u.name for u in units] == ["unit-1", "unit-2", "unit-3", "unit-4"]
    for u in units:
        assert len(u.focus_classes) == 1
        # skeleton_class_index 是完整索引副本
        assert set(u.skeleton_class_index.keys()) == set(classes)
    # 所有 .java 路径恰好覆盖一次
    all_files = [f for u in units for f in u.focus_files]
    assert sorted(all_files) == sorted(java_files)


def test_split_file_list_preserves_non_class_files():
    """Explorer 输出含 XML mapper + SQL 脚本时, split 后所有文件均保留 (core regression)."""
    files = ["src/x/Order.java", "src/x/OrderMapper.xml", "db/migration/V1.sql"]
    classes = ["Order"]
    ci = {"Order": "src/x/Order.java"}
    units = split_file_list(files, classes, ci, max_files_per_unit=10)
    all_files = {f for u in units for f in u.focus_files}
    # 全部三条路径必须幸存 — XML/SQL 曾因 bug 被静默丢弃
    assert "src/x/Order.java" in all_files
    assert "src/x/OrderMapper.xml" in all_files, "OrderMapper.xml 被丢弃 (bug 未修复)"
    assert "db/migration/V1.sql" in all_files, "V1.sql 被丢弃 (bug 未修复)"


def test_split_file_list_extra_files_not_duplicated():
    """文件切片: 3 files, max=1 → 3 units. XML 只出现在 1 个 unit, 不重复."""
    classes = ["Order", "Customer"]
    ci = {"Order": "src/Order.java", "Customer": "src/Customer.java"}
    files = list(ci.values()) + ["src/OrderMapper.xml"]
    units = split_file_list(files, classes, ci, max_files_per_unit=1)
    assert len(units) == 3
    xml_count = sum(u.focus_files.count("src/OrderMapper.xml") for u in units)
    assert xml_count == 1, f"OrderMapper.xml 出现了 {xml_count} 次, 应恰好 1 次"
    # unit-1: Order.java + Order class, unit-2: Customer.java + Customer class, unit-3: XML only
    assert units[0].focus_classes == ["Order"]
    assert units[1].focus_classes == ["Customer"]
    assert units[2].focus_classes == []
    assert units[2].focus_files == ["src/OrderMapper.xml"]
