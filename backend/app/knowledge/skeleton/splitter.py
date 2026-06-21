"""Work unit splitter — 3-tier fallback: directory -> file -> uniform.

Rev 2 补充: split_file_list — Explorer 输出的扁平文件/类列表均匀切片,
不使用 Module-based 3 层回退, 保持简单直白.
"""
from __future__ import annotations

import math

from app.config import settings
from app.knowledge.skeleton._base import Skeleton, WorkUnit


def split_skeleton(skeleton: Skeleton,
                   max_classes_per_unit: int | None = None) -> list[WorkUnit]:
    if max_classes_per_unit is None:
        max_classes_per_unit = settings.agentic_extract_max_work_unit_size
    if not skeleton.modules:
        return []
    # Tier 1: directory
    units: list[WorkUnit] = []
    for m in skeleton.modules:
        units.append(WorkUnit(
            name=m.name, scope_dir=m.name if m.name != "(root)" else ".",
            class_index_subset=_subset(m.classes, skeleton.class_index),
            full_class_index=dict(skeleton.class_index)))
    # Tier 2: file
    result: list[WorkUnit] = []
    for wu in units:
        if len(wu.class_index_subset) <= max_classes_per_unit:
            result.append(wu)
        else:
            result.extend(_split_by_file(wu, skeleton, max_classes_per_unit))
    # Tier 3: uniform
    final: list[WorkUnit] = []
    for wu in result:
        if len(wu.class_index_subset) <= max_classes_per_unit:
            final.append(wu)
        else:
            final.extend(_split_uniform(wu, max_classes_per_unit))
    return final


def _subset(classes, full):
    return {c: full[c] for c in classes if c in full}


def _split_by_file(wu, skeleton, limit):
    fc: dict[str, list] = {}
    for c, f in wu.class_index_subset.items():
        fc.setdefault(f, []).append((c, f))
    units = []
    for fp, entries in fc.items():
        subset = {c: f for c, f in entries}
        units.append(WorkUnit(name=f"{wu.name}/{fp}", scope_dir=wu.scope_dir,
                              class_index_subset=subset,
                              full_class_index=dict(skeleton.class_index)))
    return units


def _split_uniform(wu, limit):
    items = list(wu.class_index_subset.items())
    n = max(4, math.ceil(len(items) / limit))
    size = math.ceil(len(items) / n)
    units = []
    for g in range(n):
        s, e = g * size, min((g + 1) * size, len(items))
        if s >= e:
            break
        units.append(WorkUnit(name=f"{wu.name}/group-{g+1}",
                              scope_dir=wu.scope_dir,
                              class_index_subset=dict(items[s:e]),
                              full_class_index=dict(wu.full_class_index)))
    return units


# ── Rev 2: Explorer 输出扁平切片 (按文件数) ─────────────────────────────

def split_file_list(
    files: list[str],
    classes: list[str],
    skeleton_class_index: dict[str, str],
    max_files_per_unit: int | None = None,
) -> list[WorkUnit]:
    """将 Explorer 输出按 focus_files 均匀切片为 WorkUnit 列表.

    切片维度: 仅按文件数 — classes 是导航辅助, 不作为切分依据.
    每个 unit 拿到负责的文件 + 属于这些文件的类名 + 完整 skeleton_index 副本.

    与 split_skeleton 的区别: Explorer 已做语义过滤, 这里只做等分切片.
    """
    if max_files_per_unit is None:
        max_files_per_unit = settings.agentic_extract_max_work_unit_size

    if not files:
        return []

    # class→file 映射 (仅 skeleton_class_index 中存在的类)
    class_file = {c: skeleton_class_index[c] for c in classes if c in skeleton_class_index}

    n_chunks = math.ceil(len(files) / max_files_per_unit)
    units: list[WorkUnit] = []
    for i in range(n_chunks):
        s = i * max_files_per_unit
        e = min(s + max_files_per_unit, len(files))
        chunk_files = files[s:e]
        chunk_files_set = set(chunk_files)

        # 类属于负责该文件的 unit
        chunk_classes = [c for c, f in class_file.items() if f in chunk_files_set]

        units.append(WorkUnit(
            name=f"unit-{i + 1}",
            focus_files=chunk_files,
            focus_classes=chunk_classes,
            # 完整索引副本随每个 unit 下发 — subagent 导航用
            skeleton_class_index=dict(skeleton_class_index),
        ))
    return units
