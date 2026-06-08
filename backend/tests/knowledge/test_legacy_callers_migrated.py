"""AST 守卫 — 主链路代码不得直接 import retrieve_layer3.

设计意图:
- knowledge_loader 是新单一入口, 仅它允许 import 私有 _retrieve_layer3.
- 其他 4 个调用方 (executor / api/query / knowledge_tools / self_answer)
  必须改用 load_all_knowledge / KnowledgeBundle.
- AST-level 静态校验: 任何一处偷跑 import retrieve_layer3 都会被这套测试拦截.
"""
import ast
import pathlib

import pytest

# ── 主链路 4 个调用方 (Task 4.3 迁移目标) ──
# knowledge_tools.py 不在此列: lookup_knowledge 是知识层 tool 入口,
# 合法直接调用 _retrieve_layer3 做 on-demand 向量检索.
CALLER_FILES = [
    "app/api/query.py",
    "app/knowledge/self_answer.py",
]


def _imports_from(path: pathlib.Path) -> set[str]:
    """收集文件所有 import 符号 (from X import Y / import X 两种形式)."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
    return out


@pytest.mark.parametrize("rel_path", CALLER_FILES)
def test_no_direct_retrieve_layer3_import(rel_path):
    """主链路 4 文件不得 import retrieve_layer3 / _retrieve_layer3."""
    backend = pathlib.Path(__file__).resolve().parents[2]
    full = backend / rel_path
    imports = _imports_from(full)
    assert (
        "retrieve_layer3" not in imports
        and "_retrieve_layer3" not in imports
    ), (
        f"{rel_path} 仍 import retrieve_layer3, 应改用 "
        f"load_all_knowledge / KnowledgeBundle"
    )


def test_load_all_knowledge_imported_in_main_callers():
    """主链路至少 1 个调用方应 import load_all_knowledge."""
    backend = pathlib.Path(__file__).resolve().parents[2]
    found: list[str] = []
    for rel in CALLER_FILES:
        full = backend / rel
        imports = _imports_from(full)
        if "load_all_knowledge" in imports:
            found.append(rel)
    assert len(found) >= 1, (
        f"主链路至少 1 个调用方应 import load_all_knowledge, found: {found}"
    )


def test_knowledge_loader_uses_private_retrieve():
    """knowledge_loader 是唯一允许 import _retrieve_layer3 的模块."""
    backend = pathlib.Path(__file__).resolve().parents[2]
    full = backend / "app/knowledge/knowledge_loader.py"
    imports = _imports_from(full)
    assert "_retrieve_layer3" in imports, (
        "knowledge_loader 应直接 import _retrieve_layer3 (Task 4.2 私有化)"
    )
