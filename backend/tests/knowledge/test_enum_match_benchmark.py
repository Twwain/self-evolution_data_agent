# backend/tests/knowledge/test_enum_match_benchmark.py
"""
4 repo 实测命中率 benchmark.
慢测试, 仅在 backend/data/repos/{3,5,6,7} 存在时跑.
"""
import re
from pathlib import Path

import pytest

from app.knowledge.code_parser import (
    _build_class_index,
    _scan_dao_collection_mappings,
    _scan_document_classes,
    _scan_files,
)
from app.knowledge.enum_extractor import (
    BASE_TYPES,
    EnumDef,
    EnumValue,
    _field_root_and_suffix,
    _resolve_enum_class,
)

RE_FIELD = re.compile(r"private\s+(\w+(?:<[^>]+>)?)\s+(\w+)\s*;")
RE_ENUM = re.compile(r"^\s*public\s+enum\s+(\w+)", re.M)

_REPOS_BASE = Path("data/repos")
_REPOS_AVAILABLE = all((_REPOS_BASE / str(i)).exists() for i in [3, 5, 6, 7])


@pytest.mark.slow
@pytest.mark.parametrize("repo_id,min_cand,min_match", [
    (3, 70, 30),
    (5, 100, 21),
    (6, 35, 8),
    (7, 90, 18),
])
def test_repo_benchmark(repo_id: int, min_cand: int, min_match: int):
    if not _REPOS_AVAILABLE:
        pytest.skip("repos 未拉到本地")

    repo = f"data/repos/{repo_id}"
    java_files, _ = _scan_files(repo)
    annotated = _scan_document_classes(java_files)
    dao_map, dao_refs = _scan_dao_collection_mappings(java_files)
    _, simple_index = _build_class_index(java_files)

    seed_paths: set[str] = set()
    hidden: dict[str, str] = {}
    for cls in dao_refs - annotated:
        coll = dao_map.get(cls)
        paths = simple_index.get(cls, [])
        if coll and paths:
            hidden[cls] = coll
    for cls in annotated:
        for p in simple_index.get(cls, []):
            seed_paths.add(p)
    for cls in hidden:
        for p in simple_index.get(cls, []):
            seed_paths.add(p)

    # Build enum_class_index from regex scan
    enum_class_index: dict[str, EnumDef] = {}
    for jf in java_files:
        try:
            text = Path(jf).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name in RE_ENUM.findall(text):
            enum_class_index[name] = EnumDef(
                enum_class=name,
                fully_qualified_name=name,
                values=[EnumValue(name="X", db_value=0, description="")],
            )

    cand = 0
    matched = 0
    for seed in seed_paths:
        try:
            text = Path(seed).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in RE_FIELD.finditer(text):
            ftype, fname = m.group(1), m.group(2)
            field = {"type": ftype, "field": fname}
            _, f_suf = _field_root_and_suffix(fname)
            inner = re.sub(r"<.*>", "", ftype)
            if inner not in BASE_TYPES or not f_suf:
                continue
            cand += 1
            ec, src = _resolve_enum_class(field, enum_class_index)
            if ec:
                matched += 1

    assert cand >= min_cand, f"repo {repo_id}: candidates {cand} < {min_cand}"
    assert matched >= min_match, f"repo {repo_id}: matched {matched} < {min_match}"
