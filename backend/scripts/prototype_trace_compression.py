"""trace 压缩方案原型 — 把 80KB 原始 trace_json 压成 ~3KB 结构化摘要给 LLM.

设计原则:
1. **保全部 18 步骨架** — tool name / target / pipeline shape / 关键过滤字段 / row 返回数
2. **剔除巨型 output** — fields 完整描述 / ObjectId 长列表 / 完整 enum_values
3. **三段独立** — trace_summary (按步骨架) + known_facts (LLM 提炼时的禁区) + inflection_points (转折点种子)

输出到项目根 tmp/trace_compression_<trace_id>.{md,json}, 用户审核压缩质量后再决定是否落地.

用法:
    cd backend && python -m scripts.prototype_trace_compression
    cd backend && python -m scripts.prototype_trace_compression --trace-id <id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.metadata import async_session
from app.models.agent_trace import AgentTrace


# ════════════════════════════════════════════
#  Pipeline 形状归纳 (剔除字面量, 只保 stage 操作符 + 关键字段)
# ════════════════════════════════════════════

def summarize_pipeline(pipeline: list[dict]) -> str:
    """把 mongo pipeline 摘成单行 signature.

    e.g. [{'$match': {auditStatus:0, docId:{$in:[...80 ids]}}}, {'$unwind': '$groups'}, ...]
         → '$match(auditStatus,docId)→$unwind($groups)→$unwind($groups.resources)→$group(_id=$groups.resources.resourceType)'
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
    """单 stage 摘要 — 保操作符 + 关键字段名/路径, 丢具体字面量.

    设计原则: 未知 stage 走兜底 (提 body 顶层字段名), 不会丢任何 stage.
    """
    if op == "$match" and isinstance(body, dict):
        return f"{op}({','.join(sorted(body.keys()))})"
    if op == "$group" and isinstance(body, dict):
        gid = body.get("_id")
        if isinstance(gid, str):
            # 保完整字段路径 (e.g. $groups.resources.resourceType)
            gid_repr = gid
        elif isinstance(gid, dict):
            # 复合 _id, 列字段
            gid_repr = f"{{{','.join(sorted(gid.keys()))}}}"
        else:
            gid_repr = "null" if gid is None else "expr"
        return f"{op}(_id={gid_repr})"
    if op == "$unwind":
        if isinstance(body, str):
            path = body  # e.g. "$groups.resources" — 已含完整路径
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
    if op == "$limit" or op == "$skip" or op == "$sample" or op == "$count":
        return f"{op}({body!r}[:40])" if not isinstance(body, (int, str)) else f"{op}({body})"
    if op == "$facet" and isinstance(body, dict):
        return f"{op}({','.join(sorted(body.keys()))})"
    if op == "$addFields" and isinstance(body, dict):
        return f"{op}({','.join(sorted(body.keys()))})"
    if op == "$set" and isinstance(body, dict):
        return f"{op}({','.join(sorted(body.keys()))})"
    if op == "$replaceRoot" and isinstance(body, dict):
        nr = body.get("newRoot")
        return f"{op}({nr if isinstance(nr, str) else 'expr'})"
    # 兜底: 未知 stage 提 body 顶层字段名, 不丢信息
    if isinstance(body, dict):
        return f"{op}({','.join(sorted(body.keys())[:5])})"
    return op


# ════════════════════════════════════════════
#  Tool call 压缩 — 按 tool 类型不同压法
# ════════════════════════════════════════════

def compact_tool_call(idx: int, call: dict) -> dict:
    """把单 tool call 压成 ~10 字段 dict.

    保: idx / name / target / 关键 input / 关键 output 指标.
    剔: 完整 schema fields / 巨型 enum_values / ObjectId 列表 / sample docs.

    **维护提示**: 新加 agent tool 时, 在 per-tool 压缩策略下加一个 elif 分支.
    未匹配的 tool 会走通用兜底 — 不会炸但信息丢失.
    """
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

    # ── per-tool 压缩策略 ──
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
                if isinstance(f, dict) and (f.get("sub_fields") or "List" in (f.get("type") or ""))
            ]

    elif name == "execute_query":
        rec["mode"] = inp.get("mode")
        q = inp.get("query") or {}
        if isinstance(q, dict):
            # MongoDB 路径
            pipeline = q.get("pipeline")
            if pipeline:
                rec["pipeline_signature"] = summarize_pipeline(pipeline)
                rec["pipeline_stage_count"] = len(pipeline)
            elif q.get("filter") is not None:
                rec["filter_fields"] = sorted((q.get("filter") or {}).keys())
            # MySQL 路径 — query 直接含 sql 字符串
            sql = q.get("sql")
            if isinstance(sql, str) and sql:
                rec["sql_signature"] = _summarize_sql(sql)
        elif isinstance(q, str):
            # 退化: query 直接是 SQL 字符串
            rec["sql_signature"] = _summarize_sql(q)
        # output 指标
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
                rec["recalled_ke_ids"] = [h.get("entry_id") for h in hits if isinstance(h, dict)]
                rec["recalled_summaries"] = [
                    (h.get("content") or "")[:60] for h in hits[:5] if isinstance(h, dict)
                ]

    elif name == "recommend_chart":
        rec["chart_type"] = (out or {}).get("chart_type") if isinstance(out, dict) else None
        rec["category_column"] = inp.get("category_column")

    elif name == "execute_plan":
        plan = inp.get("plan") or {}
        steps = plan.get("steps") or []
        rec["plan_step_count"] = len(steps)
        # 列每个 step 的 target 序列, 让 LLM 看到 plan 的导航链
        rec["plan_collections"] = [
            s.get("collection") for s in steps if isinstance(s, dict) and s.get("collection")
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
        # trace_refiner 场景几乎不会见到 (agent 主动 save 罕见)
        rec["entry_type"] = inp.get("entry_type")

    # 未匹配的 tool: 走通用兜底, rec 仅含 step+name(+target/db_type)
    if error:
        rec["error"] = str(error)[:200]
    return rec


def _summarize_sql(sql: str) -> str:
    """SQL 摘要 — 保动词 + FROM 表名 + WHERE 字段名, 丢字面量."""
    import re
    s = sql.strip()
    verb_match = re.match(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", s, re.IGNORECASE)
    verb = verb_match.group(1).upper() if verb_match else "?"
    tables = re.findall(r"\bFROM\s+([\w.`]+)", s, re.IGNORECASE)
    joins = re.findall(r"\bJOIN\s+([\w.`]+)", s, re.IGNORECASE)
    where_cols = re.findall(r"\bWHERE\s+(.+?)(?:\bGROUP|\bORDER|\bLIMIT|$)", s, re.IGNORECASE | re.DOTALL)
    where_fields: list[str] = []
    if where_cols:
        where_fields = sorted(set(re.findall(r"\b([a-zA-Z_][\w.]*)\s*(?:=|>|<|IN|LIKE)", where_cols[0])))
    parts = [verb]
    if tables:
        parts.append(f"FROM={','.join(tables)}")
    if joins:
        parts.append(f"JOIN={','.join(joins)}")
    if where_fields:
        parts.append(f"WHERE=({','.join(where_fields[:6])})")
    return " ".join(parts)


# ════════════════════════════════════════════
#  转折点识别 (确定性规则, 无 LLM)
# ════════════════════════════════════════════

def detect_inflection_points(compact_steps: list[dict]) -> list[dict]:
    """从压缩后的 steps 识别 trajectory 转折点.

    3 类:
    - target_switch: 同会话内 target 首次到达新集合 (回头切换视为噪音过滤)
    - pipeline_complexity_jump: pipeline_stage_count 显著增加 (>= +2 且 ≥ 4 stages)
    - retry_after_empty: 同 target 上一次返空后, 下一次改 pipeline 又试
    """
    ip: list[dict] = []

    # target_switch — 只保"首次到达新 target", 过滤回头噪音
    seen_targets: set[str] = set()
    last_target: str | None = None
    for s in compact_steps:
        if s.get("tool") not in ("execute_query", "fetch_schema", "inspect_values"):
            continue
        tgt = s.get("target")
        if not tgt:
            continue
        if last_target is not None and tgt != last_target and tgt not in seen_targets:
            # 首次到达新 target
            ip.append({
                "type": "target_switch",
                "from_step": _find_prev_step_for_target(compact_steps, s["step"], last_target),
                "to_step": s["step"],
                "from_target": last_target,
                "to_target": tgt,
                "hint": f"agent 首次从 {last_target} 切到 {tgt}, "
                        f"可能用户口语集合 ≠ 数据实际所在集合",
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
                "hint": f"pipeline 从 {prev_stage_count} stages 跳到 {sc} stages, "
                        f"agent 发现简单查询不够, 升级到嵌套展开",
            })
        prev_stage_count = sc
        prev_target = s.get("target")

    # retry_after_empty (同 target 上一次返空, 下一次改 pipeline_signature)
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
            "hint": f"step {prev['step']} 在 {curr.get('target')} 上返空, "
                    f"step {curr['step']} 改 pipeline 重试",
        })

    return ip


def _find_prev_step_for_target(steps: list[dict], current_step: int, target: str) -> int | None:
    for s in reversed(steps):
        if s["step"] >= current_step:
            continue
        if s.get("target") == target:
            return s["step"]
    return None


# ════════════════════════════════════════════
#  Known facts 提取 (告诉 LLM 什么是"已知信息, 不要重复提炼")
# ════════════════════════════════════════════

def extract_known_facts(compact_steps: list[dict]) -> dict:
    """从 trace 中拢出"LLM 提炼时不该复述的已知信息":

    - known_schemas: fetch_schema 拿到的 collection → 字段名集合
    - schema_known_enum_fields: collection.field 哪些已经在 schema 标注 enum_values (不要再产 rule 复述)
    - recalled_kes: lookup_knowledge 已召回 KE 摘要 (不要重复提炼)
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
    }


# ════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════

async def load_trace(trace_id: str) -> AgentTrace:
    async with async_session() as db:
        r = await db.execute(select(AgentTrace).where(AgentTrace.trace_id == trace_id))
        t = r.scalar_one_or_none()
        if t is None:
            raise SystemExit(f"trace 不存在: {trace_id}")
        return t


def compress(trace: AgentTrace) -> dict:
    raw = trace.trace_json or "{}"
    tj = json.loads(raw) if isinstance(raw, str) else raw
    tool_trace = tj.get("tool_trace") or []
    compact_steps = [compact_tool_call(i, c) for i, c in enumerate(tool_trace)]
    return {
        "trace_id": trace.trace_id,
        "user_query": trace.user_query,
        "raw_size_chars": len(raw),
        "tool_count": len(tool_trace),
        "trace_summary": compact_steps,
        "known_facts": extract_known_facts(compact_steps),
        "inflection_points": detect_inflection_points(compact_steps),
    }


def render_markdown(result: dict, llm_payload_str: str) -> str:
    """生成人类可读的审核报告."""
    ks = result["known_facts"]["known_schemas"]
    ef = result["known_facts"]["schema_known_enum_fields"]
    rk = result["known_facts"]["recalled_kes"]
    ip = result["inflection_points"]
    lines = [
        f"# Trace 压缩方案原型审核报告",
        "",
        f"- **trace_id**: `{result['trace_id']}`",
        f"- **user_query**: {result['user_query']}",
        f"- **原始 trace_json 大小**: {result['raw_size_chars']:,} chars",
        f"- **完整 tool 调用数**: {result['tool_count']}",
        f"- **压缩后 LLM 入参大小**: {len(llm_payload_str):,} chars "
        f"({len(llm_payload_str) * 100 // result['raw_size_chars']}% of raw)",
        "",
        "## 1. trace_summary (逐步骨架, 完整 N 步全保留)",
        "",
        "| step | tool | target | 关键摘要 |",
        "|---|---|---|---|",
    ]
    for s in result["trace_summary"]:
        keys = [k for k in s if k not in ("step", "tool", "target", "db_type", "database")]
        summary_kv = ", ".join(f"{k}={_short(s[k])}" for k in keys)
        lines.append(
            f"| {s['step']} | {s.get('tool')} | {s.get('target', '-')} | {summary_kv} |"
        )

    lines += [
        "",
        "## 2. known_facts (LLM 提炼时的『已知信息禁区』)",
        "",
        "### 2.1 known_schemas — fetch_schema 已拿到的字段 (不要复述)",
        "",
    ]
    for coll, fields in ks.items():
        lines.append(f"- **{coll}** ({len(fields)} 字段): `{', '.join(fields)}`")

    lines += [
        "",
        "### 2.2 schema_known_enum_fields — 已在 schema 标注 enum_values 的字段",
        "",
        "(LLM 不应再产 rule 重复这些枚举映射)",
        "",
    ]
    for f in ef:
        lines.append(f"- `{f}`")

    lines += [
        "",
        "### 2.3 recalled_kes — lookup_knowledge 已召回的 KE (不要重复提炼)",
        "",
    ]
    for k in rk:
        lines.append(f"- KE {k['entry_id']}: {k['summary']}")

    lines += [
        "",
        "## 3. inflection_points (LLM 提炼 route_hint/rule 的种子)",
        "",
    ]
    for p in ip:
        lines.append(f"### {p['type']} (step {p.get('from_step', '?')} → {p.get('to_step', '?')})")
        lines.append("")
        for k, v in p.items():
            if k == "type":
                continue
            lines.append(f"- **{k}**: {v}")
        lines.append("")

    lines += [
        "## 4. LLM 实际入参 (压缩后的 JSON)",
        "",
        "```json",
        llm_payload_str,
        "```",
    ]
    return "\n".join(lines)


def _short(v: Any, limit: int = 60) -> str:
    s = repr(v) if not isinstance(v, str) else v
    return s if len(s) <= limit else s[:limit] + "…"


async def main(trace_id: str, audit_all: bool) -> int:
    if audit_all:
        return await _audit_all_traces()
    trace = await load_trace(trace_id)
    result = compress(trace)
    # LLM 实际入参 (用户消息部分)
    llm_payload = {
        "user_query": result["user_query"],
        "trace_summary": result["trace_summary"],
        "known_facts": result["known_facts"],
        "inflection_points": result["inflection_points"],
    }
    llm_payload_str = json.dumps(llm_payload, ensure_ascii=False, indent=2)

    # 输出到项目根 tmp/
    project_root = Path(__file__).resolve().parents[2]
    tmp_dir = project_root / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    short_tid = trace_id[:8]
    md_path = tmp_dir / f"trace_compression_{short_tid}.md"
    json_path = tmp_dir / f"trace_compression_{short_tid}.json"

    md_path.write_text(render_markdown(result, llm_payload_str), encoding="utf-8")
    json_path.write_text(llm_payload_str, encoding="utf-8")

    print(f"[OK] 压缩完成:")
    print(f"  原始: {result['raw_size_chars']:,} chars / {result['tool_count']} tools")
    print(f"  压缩后 LLM 入参: {len(llm_payload_str):,} chars "
          f"({len(llm_payload_str) * 100 // result['raw_size_chars']}% of raw)")
    print(f"  审核报告: {md_path.relative_to(project_root)}")
    print(f"  LLM 入参 JSON: {json_path.relative_to(project_root)}")
    return 0


async def _audit_all_traces() -> int:
    """普适性自检 — 对所有生产 trace 跑一遍压缩, 列出每条的指标和异常."""
    async with async_session() as db:
        rows = (await db.execute(
            select(AgentTrace).where(AgentTrace.status.in_(["completed", "refined"]))
        )).scalars().all()
    print(f"[AUDIT] 拉取 {len(rows)} 条 trace, 逐条压缩...\n")
    print(f"{'trace_id':12s} {'tools':>5s} {'raw_kb':>7s} {'comp_kb':>8s} "
          f"{'ratio':>6s} {'IP':>3s} {'tools_uncovered':30s}")
    print("-" * 90)
    known_tools = {
        "fetch_schema", "execute_query", "inspect_values", "lookup_knowledge",
        "recommend_chart", "execute_plan", "clarify_with_user", "estimate_cost",
        "save_knowledge",
    }
    fails: list[tuple[str, str]] = []
    for r in rows:
        try:
            res = compress(r)
            llm_payload = {
                "user_query": res["user_query"],
                "trace_summary": res["trace_summary"],
                "known_facts": res["known_facts"],
                "inflection_points": res["inflection_points"],
            }
            comp = len(json.dumps(llm_payload, ensure_ascii=False))
            raw = res["raw_size_chars"] or 1
            uncov = sorted({
                s.get("tool", "?")
                for s in res["trace_summary"]
                if s.get("tool") not in known_tools
            })
            print(f"{r.trace_id[:8]:12s} {res['tool_count']:>5d} "
                  f"{raw/1024:>7.1f} {comp/1024:>8.1f} "
                  f"{comp * 100 // raw:>5d}% "
                  f"{len(res['inflection_points']):>3d} "
                  f"{','.join(uncov)[:30]:30s}")
        except Exception as e:
            fails.append((r.trace_id, repr(e)[:100]))
            print(f"{r.trace_id[:8]:12s} [FAIL] {repr(e)[:80]}")
    print()
    if fails:
        print(f"[FAIL] {len(fails)} 条 trace 压缩抛异常:")
        for tid, err in fails:
            print(f"  {tid}: {err}")
        return 1
    print(f"[OK] 全部 {len(rows)} 条 trace 压缩通过, 无异常")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--trace-id", default="c815e1ac-208c-4b83-b404-c859d6f4f447")
    p.add_argument("--audit-all", action="store_true",
                   help="对所有生产 trace 跑一遍压缩, 验证普适性")
    args = p.parse_args()
    sys.exit(asyncio.run(main(args.trace_id, args.audit_all)))
