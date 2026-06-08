"""MyBatis 动态 SQL 全分支枚举 (Phase 2 Task 4).

设计: 03-extraction.md §3.4.1
用 xml.etree 解析 XML, 确定性枚举 <if>/<choose> 组合, 不依赖 LLM.
"""
from __future__ import annotations

import itertools
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from app.config import settings


@dataclass(slots=True)
class DynamicSQLBranch:
    sql: str
    branch_conditions: list[str] = field(default_factory=list)
    nl_hint: str = ""


def enumerate_dynamic_branches(
    select_xml: str, *, method_id: str,
) -> list[DynamicSQLBranch]:
    """全分支枚举 <if>/<choose>. 上限 settings.dynamic_sql_max_branches.

    Algorithm:
    - Find all <if> elements → boolean expansion (2^N)
    - Find all <choose> elements → each produces len(when)+1 options
    - Cartesian product of all combinations
    - Cap at dynamic_sql_max_branches
    """
    cap = settings.dynamic_sql_max_branches
    try:
        root = ET.fromstring(select_xml)
    except ET.ParseError:
        return [DynamicSQLBranch(sql=select_xml.strip(), branch_conditions=[], nl_hint="XML 解析失败")]

    # Collect top-level dynamic elements in document order
    ifs: list[Any] = list(root.iter("if"))
    chooses: list[Any] = list(root.iter("choose"))

    # If no dynamic tags, return single branch with full SQL text
    if not ifs and not chooses:
        sql = _render_full_text(root)
        return [DynamicSQLBranch(sql=sql, branch_conditions=[], nl_hint="无动态条件")]

    # Build if combinations: each <if> is either included or excluded
    if_combos: list[tuple[bool, ...]] = list(itertools.product([False, True], repeat=len(ifs)))

    # Build choose combinations: each <choose> picks exactly one arm
    choose_options: list[list[Any]] = []
    for choose in chooses:
        arms: list[Any] = []
        for child in choose:
            tag = _local_tag(child)
            if tag in ("when", "otherwise"):
                arms.append(child)
        if not arms:
            arms = [None]  # empty choose → single no-op arm
        choose_options.append(arms)

    choose_combos = list(itertools.product(*choose_options)) if choose_options else [()]

    # Build set of elements inside <choose> blocks (to skip during base rendering)
    choose_children: set[int] = set()
    for choose in chooses:
        choose_children.add(id(choose))
        for child in choose.iter():
            choose_children.add(id(child))

    branches: list[DynamicSQLBranch] = []
    seen_sqls: set[str] = set()

    for if_mask in if_combos:
        for choose_pick in choose_combos:
            if len(branches) >= cap:
                break

            # Determine which <if> elements are active
            active_ifs: set[int] = set()
            cond_labels: list[str] = []
            for idx, active in enumerate(if_mask):
                if active:
                    active_ifs.add(id(ifs[idx]))
                    test_attr = ifs[idx].get("test", "")
                    cond_labels.append(test_attr)

            # Determine which choose arms are active
            active_choose_arms: set[int] = set()
            for arm in choose_pick:
                if arm is not None:
                    active_choose_arms.add(id(arm))
                    tag = _local_tag(arm)
                    if tag == "when":
                        cond_labels.append(f"choose: {arm.get('test', '')}")
                    elif tag == "otherwise":
                        cond_labels.append("choose: otherwise")

            # Render SQL for this combination
            sql = _render_branch(root, ifs, chooses, active_ifs, active_choose_arms, choose_children)
            sql = re.sub(r"\s+", " ", sql).strip()

            if not sql:
                continue
            if sql in seen_sqls:
                continue
            seen_sqls.add(sql)

            branches.append(DynamicSQLBranch(
                sql=sql,
                branch_conditions=cond_labels,
                nl_hint=_make_nl_hint(cond_labels),
            ))
        if len(branches) >= cap:
            break

    return branches[:cap]


async def select_representative_branches(
    branches: list[DynamicSQLBranch], *, method_id: str, target_count: int,
) -> list[DynamicSQLBranch]:
    """超 cap 后, 让 LLM 在 branches 上选 N 个最具业务意义."""
    if len(branches) <= target_count:
        return branches

    import asyncio

    from app.engine.llm import chat_completion
    from app.knowledge.llm_retry import with_retry

    prompt = (
        "Given the dynamic SQL branches below, select the most business-meaningful "
        f"{target_count} indices to keep as representatives.\n"
        f"Method: {method_id}\n"
        + "\n".join(
            f"[{i}] cond={b.branch_conditions} sql={b.sql[:120]}"
            for i, b in enumerate(branches)
        )
        + '\nOutput JSON: {"selected_indices": [int]}'
    )
    raw = await with_retry(
        lambda: asyncio.to_thread(chat_completion, [{"role": "user", "content": prompt}]),
        template_name="dynamic_branch_select",
        input_meta={"method_id": method_id},
        prompt_full=prompt,
        extraction_kind="mybatis_example",
    )
    from app.engine.json_parser import parse_llm_json
    parsed = parse_llm_json(raw, expect="dict")
    if parsed is None:
        return []
    indices = parsed.get("selected_indices", [])[:target_count]
    return [branches[i] for i in indices if 0 <= i < len(branches)]


# ════════════════════════════════════════════
#  Internal helpers
# ════════════════════════════════════════════


def _local_tag(elem: Any) -> str:
    """Get local tag name without namespace."""
    tag = elem.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.split("}", 1)[1]
    return tag if isinstance(tag, str) else ""


def _render_full_text(elem: Any) -> str:
    """Render all text content from an element tree."""
    parts: list[str] = []
    parts.append(elem.text or "")
    for child in elem.iter():
        if child is not elem:
            parts.append(child.text or "")
        parts.append(child.tail or "")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _render_branch(
    root: Any,
    ifs: list[Any],
    chooses: list[Any],
    active_ifs: set[int],
    active_choose_arms: set[int],
    choose_children: set[int],
) -> str:
    """Render SQL text for a specific branch combination."""
    parts: list[str] = []

    def _walk(elem: Any) -> None:
        tag = _local_tag(elem)

        if tag == "if":
            if id(elem) in active_ifs:
                # Include this <if> content
                text = (elem.text or "").strip()
                if text:
                    parts.append(text)
                for child in elem:
                    _walk(child)
                    tail = (child.tail or "").strip()
                    if tail:
                        parts.append(tail)
            # else: skip this <if> entirely
            return

        if tag == "choose":
            # Only render the active arm
            for child in elem:
                if id(child) in active_choose_arms:
                    text = (child.text or "").strip()
                    if text:
                        parts.append(text)
                    for grandchild in child:
                        _walk(grandchild)
                        tail = (grandchild.tail or "").strip()
                        if tail:
                            parts.append(tail)
            return

        if tag == "foreach":
            # Treat foreach as single expansion with placeholder
            parts.append("?")
            return

        # For root or other container elements
        text = (elem.text or "").strip()
        if text:
            parts.append(text)

        for child in elem:
            _walk(child)
            tail = (child.tail or "").strip()
            if tail:
                parts.append(tail)

    _walk(root)
    return " ".join(parts)


def _make_nl_hint(labels: list[str]) -> str:
    """Generate a one-line Chinese summary of active conditions."""
    if not labels:
        return "无附加条件"
    return "按 " + " / ".join(labels[:3]) + (" ..." if len(labels) > 3 else "") + " 筛选"
