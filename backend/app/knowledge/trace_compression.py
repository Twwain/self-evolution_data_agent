"""trace_json LLM 投喂前的压缩视图 — 不依赖 LLM, 纯结构化解析.

设计目的:
- 80KB+ 原始 trace_json → ~15KB 结构化骨架, 保 N 步全部信息
- 喂给 trace_refiner 时, LLM 同时看到 trace_summary / known_facts / inflection_points
- 让 prompt 里的 "不要复述 schema" / "看 agent 试错转折点" 等约束有 ground truth 对照

调用方: app/api/agent_traces.py::refine_traces_endpoint

**维护提示**: 新加 agent tool 时, 在 compact_tool_call 加 elif 分支;
未匹配的 tool 走通用兜底 (rec 仅含 step+name+target), 不会炸但信息丢失.
"""
from __future__ import annotations

import re
from typing import Any


# ════════════════════════════════════════════
#  Pipeline 形状归纳
# ════════════════════════════════════════════

def summarize_pipeline(pipeline: list[dict]) -> str:
    """Mongo pipeline 摘成单行 signature.

    保: 操作符 + 字段名/完整路径 (如 $groups.resources.resourceType).
    剔: 具体字面量 (ObjectId 列表 / 字符串值).
    """
    if not isinstance(pipeline, list):
        return repr(pipeline)[:120]
    parts: list[str] = []
    for stage in pipeline:
        if not isinstance(stage, dict):
            continue
        for op, body in stage.items():
            parts.append(_stage_signature(op, body))
    return "→".join(parts) if parts else "(empty)"


def _stage_signature(op: str, body: Any) -> str:
    """单 stage 摘要 — 未知 stage 走兜底 (提 body 顶层字段名), 不会丢任何 stage."""
    if op == "$match" and isinstance(body, dict):
        return f"{op}({','.join(sorted(body.keys()))})"
    if op == "$group" and isinstance(body, dict):
        gid = body.get("_id")
        if isinstance(gid, str):
            gid_repr = gid  # 完整路径 e.g. $groups.resources.resourceType
        elif isinstance(gid, dict):
            gid_repr = f"{{{','.join(sorted(gid.keys()))}}}"
        else:
            gid_repr = "null" if gid is None else "expr"
        return f"{op}(_id={gid_repr})"
    if op == "$unwind":
        if isinstance(body, str):
            path = body
        elif isinstance(body, dict):
            path = body.get("path") or "?"
        else:
            path = "?"
        return f"{op}({path})"
    if op == "$lookup" and isinstance(body, dict):
        return (f"{op}(from={body.get('from')},"
                f"local={body.get('localField')},"
                f"foreign={body.get('foreignField')})")
    if op == "$project" and isinstance(body, dict):
        return f"{op}({len(body)}fields)"
    if op == "$sort" and isinstance(body, dict):
        return f"{op}({','.join(body.keys())})"
    if op in ("$limit", "$skip", "$sample", "$count"):
        return f"{op}({body})" if isinstance(body, (int, str)) else op
    if op in ("$facet", "$addFields", "$set") and isinstance(body, dict):
        return f"{op}({','.join(sorted(body.keys()))})"
    if op == "$replaceRoot" and isinstance(body, dict):
        nr = body.get("newRoot")
        return f"{op}({nr if isinstance(nr, str) else 'expr'})"
    if isinstance(body, dict):
        return f"{op}({','.join(sorted(body.keys())[:5])})"
    return op


def _summarize_sql(sql: str) -> str:
    """MySQL SQL 摘要 — 保 verb + FROM + JOIN + WHERE 字段名, 丢字面量."""
    s = sql.strip()
    verb_match = re.match(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", s, re.IGNORECASE)
    verb = verb_match.group(1).upper() if verb_match else "?"
    tables = re.findall(r"\bFROM\s+([\w.`]+)", s, re.IGNORECASE)
    joins = re.findall(r"\bJOIN\s+([\w.`]+)", s, re.IGNORECASE)
    where_blocks = re.findall(
        r"\bWHERE\s+(.+?)(?:\bGROUP|\bORDER|\bLIMIT|$)", s, re.IGNORECASE | re.DOTALL,
    )
    where_fields: list[str] = []
    if where_blocks:
        where_fields = sorted(set(
            re.findall(r"\b([a-zA-Z_][\w.]*)\s*(?:=|>|<|IN|LIKE)", where_blocks[0])
        ))
    parts = [verb]
    if tables:
        parts.append(f"FROM={','.join(tables)}")
    if joins:
        parts.append(f"JOIN={','.join(joins)}")
    if where_fields:
        parts.append(f"WHERE=({','.join(where_fields[:6])})")
    return " ".join(parts)


# ════════════════════════════════════════════
#  单 tool call 压缩
# ════════════════════════════════════════════

def compact_tool_call(idx: int, call: dict) -> dict:
    """单 tool call 压成 ~10 字段 dict. 不会抛异常."""
    name = call.get("name", "")
    inp = call.get("input") or {}
    out = call.get("output") or {}
    error = call.get("error")

    rec: dict[str, Any] = {"step": idx, "tool": name}

    target = inp.get("target") or inp.get("collection") or inp.get("table")
    if target:
        rec["target"] = target
    db_type = inp.get("db_type")
    database = inp.get("database")
    if db_type:
        rec["db_type"] = db_type
    if database:
        rec["database"] = database

    if name == "fetch_schema":
        fields = out.get("fields") if isinstance(out, dict) else None
        if isinstance(fields, list):
            rec["schema_field_count"] = len(fields)
            rec["schema_field_names"] = [
                f.get("name") for f in fields if isinstance(f, dict) and f.get("name")
            ]
            rec["fields_with_enum"] = [
                f.get("name") for f in fields
                if isinstance(f, dict) and f.get("enum_values")
            ]
            rec["nested_fields"] = [
                f.get("name") for f in fields
                if isinstance(f, dict) and (
                    f.get("sub_fields") or "List" in (f.get("type") or "")
                )
            ]

    elif name == "execute_query":
        rec["mode"] = inp.get("mode")
        q = inp.get("query") or {}
        if isinstance(q, dict):
            pipeline = q.get("pipeline")
            if pipeline:
                rec["pipeline_signature"] = summarize_pipeline(pipeline)
                rec["pipeline_stage_count"] = len(pipeline)
            elif q.get("filter") is not None:
                rec["filter_fields"] = sorted((q.get("filter") or {}).keys())
            sql = q.get("sql")
            if isinstance(sql, str) and sql:
                rec["sql_signature"] = _summarize_sql(sql)
        elif isinstance(q, str):
            rec["sql_signature"] = _summarize_sql(q)
        if isinstance(out, dict):
            rows = out.get("rows")
            count = out.get("count")
            if isinstance(rows, list):
                rec["rows_returned"] = len(rows)
                rec["empty_result"] = (len(rows) == 0)
            elif isinstance(count, int):
                rec["count_returned"] = count

    elif name == "inspect_values":
        rec["field"] = inp.get("field")
        if isinstance(out, dict):
            distinct = out.get("distinct_values") or out.get("values")
            if isinstance(distinct, list):
                rec["distinct_count"] = len(distinct)

    elif name == "lookup_knowledge":
        rec["query"] = (inp.get("query") or "")[:80]
        rec["types"] = inp.get("types")
        if isinstance(out, dict):
            hits = out.get("hits") or out.get("results") or []
            if isinstance(hits, list):
                rec["recalled_ke_ids"] = [
                    h.get("entry_id") for h in hits if isinstance(h, dict)
                ]
                rec["recalled_summaries"] = [
                    (h.get("content") or "")[:60] for h in hits[:5]
                    if isinstance(h, dict)
                ]

    elif name == "recommend_chart":
        rec["chart_type"] = (out or {}).get("chart_type") if isinstance(out, dict) else None
        rec["category_column"] = inp.get("category_column")

    elif name == "execute_plan":
        plan = inp.get("plan") or {}
        steps = plan.get("steps") or []
        rec["plan_step_count"] = len(steps)
        rec["plan_collections"] = [
            s.get("collection") for s in steps
            if isinstance(s, dict) and s.get("collection")
        ]

    elif name == "clarify_with_user":
        rec["question"] = (inp.get("question") or "")[:120]
        if isinstance(out, dict):
            rec["user_answer"] = (out.get("answer") or "")[:80]

    elif name == "estimate_cost":
        if isinstance(out, dict):
            rec["est_rows"] = out.get("estimated_rows") or out.get("rows")
            rec["blocked"] = out.get("blocked")

    elif name == "save_knowledge":
        rec["entry_type"] = inp.get("entry_type")

    if error:
        rec["error"] = str(error)[:200]
    return rec


# ════════════════════════════════════════════
#  Known facts 提取 (LLM 提炼时的"已知信息禁区")
# ════════════════════════════════════════════

def extract_known_facts(
    compact_steps: list[dict],
    critical_rule_contents: list[str] | None = None,
) -> dict:
    """从 trace 中拢出 LLM 提炼时不该复述的已知信息.

    Args:
        compact_steps: compact_tool_call 输出列表
        critical_rule_contents: tier=critical + status=canonical 的 KE.content 列表
            (调用方从 knowledge_loader._load_layer1_knowledge 取). 这些规则已注入
            agent 主 system prompt, trace_refiner 提炼时不应重复.
    """
    known_schemas: dict[str, list[str]] = {}
    enum_fields: list[str] = []
    recalled_kes: list[dict] = []

    for s in compact_steps:
        if s.get("tool") == "fetch_schema":
            tgt = s.get("target")
            if tgt:
                known_schemas[tgt] = s.get("schema_field_names") or []
                for f in s.get("fields_with_enum") or []:
                    enum_fields.append(f"{tgt}.{f}")
        elif s.get("tool") == "lookup_knowledge":
            ids = s.get("recalled_ke_ids") or []
            summaries = s.get("recalled_summaries") or []
            for ke_id, summary in zip(ids, summaries):
                if ke_id:
                    recalled_kes.append({"entry_id": ke_id, "summary": summary})
    return {
        "known_schemas": known_schemas,
        "schema_known_enum_fields": sorted(set(enum_fields)),
        "recalled_kes": recalled_kes,
        "system_prompt_critical_rules": critical_rule_contents or [],
    }


# ════════════════════════════════════════════
#  转折点识别 (确定性规则, 无 LLM)
# ════════════════════════════════════════════

def detect_inflection_points(compact_steps: list[dict]) -> list[dict]:
    """识别 trajectory 转折点 — agent 试错学到的地方."""
    ip: list[dict] = []

    # target_switch — 首次到达新 target, 过滤回头噪音
    seen_targets: set[str] = set()
    last_target: str | None = None
    for s in compact_steps:
        if s.get("tool") not in ("execute_query", "fetch_schema", "inspect_values"):
            continue
        tgt = s.get("target")
        if not tgt:
            continue
        if last_target is not None and tgt != last_target and tgt not in seen_targets:
            ip.append({
                "type": "target_switch",
                "from_step": _find_prev_step_for_target(
                    compact_steps, s["step"], last_target,
                ),
                "to_step": s["step"],
                "from_target": last_target,
                "to_target": tgt,
                "hint": (f"agent 首次从 {last_target} 切到 {tgt}, "
                         f"可能用户口语集合 ≠ 数据实际所在集合"),
            })
        seen_targets.add(tgt)
        last_target = tgt

    # pipeline_complexity_jump
    prev_stage_count: int | None = None
    prev_target: str | None = None
    for s in compact_steps:
        if s.get("tool") != "execute_query":
            continue
        sc = s.get("pipeline_stage_count")
        if not isinstance(sc, int):
            continue
        if prev_stage_count is not None and sc >= prev_stage_count + 2 and sc >= 4:
            ip.append({
                "type": "pipeline_complexity_jump",
                "to_step": s["step"],
                "from_stage_count": prev_stage_count,
                "to_stage_count": sc,
                "from_target": prev_target,
                "to_target": s.get("target"),
                "new_signature": s.get("pipeline_signature"),
                "hint": (f"pipeline 从 {prev_stage_count} stages 跳到 {sc} stages, "
                         f"agent 发现简单查询不够, 升级到嵌套展开"),
            })
        prev_stage_count = sc
        prev_target = s.get("target")

    # retry_after_empty
    for i in range(1, len(compact_steps)):
        prev, curr = compact_steps[i - 1], compact_steps[i]
        if prev.get("tool") != "execute_query" or curr.get("tool") != "execute_query":
            continue
        if prev.get("target") != curr.get("target"):
            continue
        if not (prev.get("empty_result") or prev.get("rows_returned") == 0):
            continue
        if prev.get("pipeline_signature") == curr.get("pipeline_signature"):
            continue
        ip.append({
            "type": "retry_after_empty",
            "from_step": prev["step"],
            "to_step": curr["step"],
            "target": curr.get("target"),
            "prev_signature": prev.get("pipeline_signature"),
            "new_signature": curr.get("pipeline_signature"),
            "hint": (f"step {prev['step']} 在 {curr.get('target')} 上返空, "
                     f"step {curr['step']} 改 pipeline 重试"),
        })

    return ip


def _find_prev_step_for_target(
    steps: list[dict], current_step: int, target: str,
) -> int | None:
    for s in reversed(steps):
        if s["step"] >= current_step:
            continue
        if s.get("target") == target:
            return s["step"]
    return None


# ════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════

def summarize_trace_for_llm(
    trace_json_str: str,
    critical_rule_contents: list[str] | None = None,
) -> dict:
    """trace_json → 三段结构化压缩, 直接喂 LLM 的 user message.

    Returns:
        {
          "trace_summary": [...],         # N 步骨架
          "known_facts": {...},           # 已知禁区
          "inflection_points": [...],     # 转折点种子
        }
    """
    import json as _json
    try:
        tj = _json.loads(trace_json_str) if isinstance(trace_json_str, str) else (
            trace_json_str or {}
        )
    except (_json.JSONDecodeError, TypeError):
        tj = {}
    tool_trace = tj.get("tool_trace") if isinstance(tj, dict) else None
    if not isinstance(tool_trace, list):
        tool_trace = []
    compact_steps = [compact_tool_call(i, c) for i, c in enumerate(tool_trace)]
    return {
        "trace_summary": compact_steps,
        "known_facts": extract_known_facts(compact_steps, critical_rule_contents),
        "inflection_points": detect_inflection_points(compact_steps),
    }
