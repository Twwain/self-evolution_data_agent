"""Agentic repo schema extraction — agent run loop.

Uses existing agent_loop.py message protocol + chat_completion_with_tools from engine/llm.py.
No new module reinvention — only prompt + tool specs + emit handler are extraction-specific.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from string import Template
from typing import Any, Callable

from app.config import settings
from app.engine.llm import chat_completion_with_tools
from app.knowledge.extraction_emit import validate_emit
from app.knowledge.extraction_prompts import load_prompt_or_fallback
from app.knowledge.extraction_tools import (
    EXTRACTION_TOOL_SPECS,
    find_files,
    grep,
    list_dir,
    read_file,
)

logger = logging.getLogger(__name__)

# ── Base Prompt ───────────────────────────────────────────
# Source: backend/prompts/extraction-agent-base.md → loaded via prompt_loader.
# DO NOT modify prompt content without spec review + pa audit + file update.


def build_extraction_system_prompt(hint_text: str | None = None) -> str:
    """组装 system prompt: base (从 prompts/ 经 prompt_loader 加载) + 可选 profile hint."""
    body = load_prompt_or_fallback("extraction-agent-base")
    prompt = Template(body).safe_substitute(max_depth=str(settings.agentic_extract_max_depth))
    if hint_text and hint_text.strip():
        prompt += f"\n\n[Hint]\n{hint_text.strip()}"
    return prompt.strip()


@dataclass
class ExtractionResult:
    objects: list[dict] = field(default_factory=list)
    knowledge_proposals: list[dict] = field(default_factory=list)
    status: str = "ok"       # ok | partial | failed
    reason: str = ""


# ── emit_knowledge payload schema ──
# 每类 entry_type 的最小 payload 字段定义.
_KNOWLEDGE_PAYLOAD_SCHEMA: dict[str, dict] = {
    "route_hint":  {"required": ["mapper_namespace", "canonical_sql"]},
    "terminology": {"required": ["term"]},
    "rule":        {"required": ["rule_text"]},
    "example":     {"required": ["sql_pattern", "tables"]},
}


def _serialize_tool_result(result: dict) -> str:
    """序列化工具结果给 LLM — 结构化 JSON 字符串，截断超长输出防 token 爆炸."""
    raw = json.dumps(result, ensure_ascii=False, default=str)
    max_chars = settings.agent_tool_result_max_chars
    if len(raw) <= max_chars:
        return raw
    return (raw[:max_chars]
            + f"\n\n[输出截断: {len(raw)} 字符 → {max_chars}, "
            + "请用 offset/limit 缩小 read_file / 更精确的 grep 模式 / 更窄的 find_files glob]")


async def run_extraction_agent(
    *,
    repo_path: str,
    hint_text: str | None = None,
    max_iterations: int | None = None,
    repo_name: str = "",
) -> ExtractionResult:
    """运行一次 agentic schema 提取。

    Args:
        repo_path: clone 后的仓库本地路径
        hint_text: 可选 profile hint, 注入 system prompt
        max_iterations: loop 轮数上限, 默认取 IS_AGENTIC_EXTRACT_MAX_ITERATIONS
        repo_name: 仓库名, 日志分组用 (如 question-center-service)
    """
    if max_iterations is None:
        max_iterations = settings.agentic_extract_max_iterations

    system_prompt = build_extraction_system_prompt(hint_text)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            f"分析仓库 {repo_path} 中的所有数据持久化定义。"
            "按探索原则自主发现实体、递归展开字段、提取枚举、标记关联关系。"
            "完成后调用 emit_schema_object 提交每个对象。"
        )},
    ]

    emitted: list[dict] = []
    knowledge_proposals: list[dict] = []  # sql2nl/example/route_hint/terminology/rule

    def _make_emit_handler(buf: list[dict]) -> Callable:
        def _handler(**data: Any) -> dict:
            result = validate_emit(data)
            if result.status == "ok":
                buf.append(data)
                return {"status": "ok", "message": f"已提交: {data.get('name', '?')}"}
            return result.__dict__
        return _handler

    def _emit_knowledge_handler(**data: Any) -> dict:
        entry_type = data.get("entry_type", "")
        schema = _KNOWLEDGE_PAYLOAD_SCHEMA.get(entry_type)
        if schema is None:
            return {"status": "error", "message": f"无效 entry_type: {entry_type}"}
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            return {"status": "error", "message": "payload 必须是 dict"}
        missing = [k for k in schema["required"] if k not in payload or not payload[k]]
        if missing:
            return {"status": "error", "message": f"payload 缺少必填字段: {missing}"}
        knowledge_proposals.append({"entry_type": entry_type, "payload": payload})
        return {"status": "ok", "message": f"已提交 knowledge: {entry_type}"}

    tool_fns: dict[str, Callable] = {
        "list_dir": lambda **kw: list_dir(kw.get("path", "."), repo_path),
        "read_file": lambda **kw: read_file(kw["path"], repo_path, kw.get("offset"), kw.get("limit")),
        "grep": lambda **kw: grep(kw["pattern"], kw.get("path", "."), repo_path, kw.get("recursive", True)),
        "find_files": lambda **kw: find_files(kw["glob"], repo_path),  # root server-injected, LLM 只传 glob
        "emit_schema_object": _make_emit_handler(emitted),
        "emit_knowledge": _emit_knowledge_handler,
    }

    iteration = 0
    llm_failed = False
    loop_start = time.time()
    dead_loop_window = settings.agentic_extract_dead_loop_window
    # (tool_name, input_hash, error_type_or_ok) — 成功="ok", 错误=error_type
    recent_tool_calls: list[tuple[str, str, str]] = []

    while iteration < max_iterations:
        iteration += 1
        try:
            response = await chat_completion_with_tools(
                messages=messages,
                tools=EXTRACTION_TOOL_SPECS,
            )
        except Exception:
            logger.exception("chat_completion_with_tools 调用异常 (iteration=%d)", iteration)
            llm_failed = True  # LLM 调用失败 — 保留已 emit 对象, 下游识别 failed 状态
            break

        if not response.tool_calls:
            logger.info(
                "[%s] agent loop 自然终止  iteration=%-3d  objects=%-3d  knowledge=%-2d  elapsed=%.1fs",
                repo_name, iteration, len(emitted), len(knowledge_proposals),
                time.time() - loop_start,
            )
            break  # LLM 无更多工具调用 → 自然终止

        # 执行工具 + 收集结果 (先执行, 后拼消息)
        tool_results = []
        dead_loop_hit = False
        for tc in response.tool_calls:
            fn = tool_fns.get(tc.name)
            if fn is None:
                result = {"status": "error", "error_type": "UNKNOWN_TOOL",
                          "message": f"未知工具: {tc.name}"}
            else:
                try:
                    result = fn(**tc.input)
                except Exception as e:
                    result = {"status": "error", "error_type": type(e).__name__, "message": str(e)}

            # ── dead_loop 检测 (内联重实现, 3-tuple, 非 import — I1) ──
            tc_sig = (
                tc.name,
                json.dumps(tc.input, sort_keys=True, default=str),
                result.get("error_type", "") if result.get("status") == "error" else "ok",
            )
            recent_tool_calls.append(tc_sig)
            if len(recent_tool_calls) > dead_loop_window:
                recent_tool_calls.pop(0)
            if len(recent_tool_calls) >= dead_loop_window and \
               all(item == recent_tool_calls[-1] for item in recent_tool_calls[-dead_loop_window:]):
                dead_loop_hit = True
                tool_results.append((tc, result))
                break

            tool_results.append((tc, result))

        # 逐轮进度日志 — 工具执行后, objects/knowledge 反映本轮真效果
        tool_names = [tc.name for tc in response.tool_calls]
        logger.info(
            "[%s] agent loop iteration=%-3d  tools=%-40s  objects=%-3d  knowledge=%-2d  elapsed=%.1fs",
            repo_name, iteration, str(tool_names), len(emitted), len(knowledge_proposals),
            time.time() - loop_start,
        )

        # ── 单条 assistant message 包含全部已处理的 tool_calls (对齐 agent_loop.py:367-375) ──
        processed_tcs = [tc for tc, _ in tool_results]
        messages.append({
            "role": "assistant",
            "content": response.text or "",
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "input": tc.input}
                for tc in processed_tcs
            ],
        })
        for tc, result in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _serialize_tool_result(result),
            })

        if dead_loop_hit:
            return ExtractionResult(
                objects=emitted,
                knowledge_proposals=knowledge_proposals,
                status="partial",
                reason="dead_loop",
            )

    # ── 最终状态判定 — 优先级: LLM 异常 > iteration cap > 自然终止 ──
    elapsed = time.time() - loop_start
    if llm_failed:
        logger.warning(
            "[%s] agent loop 异常退出  iterations=%d  objects=%d  knowledge=%d  elapsed=%.1fs  reason=llm_call_failed",
            repo_name, iteration, len(emitted), len(knowledge_proposals), elapsed,
        )
        return ExtractionResult(
            objects=emitted,
            knowledge_proposals=knowledge_proposals,
            status="failed",
            reason="llm_call_failed",
        )
    if iteration >= max_iterations:
        logger.warning(
            "[%s] agent loop 达迭代上限  iterations=%d/%d  objects=%d  knowledge=%d  elapsed=%.1fs",
            repo_name, iteration, max_iterations, len(emitted), len(knowledge_proposals), elapsed,
        )
    else:
        logger.info(
            "[%s] agent loop 完成  iterations=%d  objects=%d  knowledge=%d  elapsed=%.1fs",
            repo_name, iteration, len(emitted), len(knowledge_proposals), elapsed,
        )
    return ExtractionResult(
        objects=emitted,
        knowledge_proposals=knowledge_proposals,
        status="partial" if iteration >= max_iterations else "ok",
        reason="iteration_cap" if iteration >= max_iterations else "",
    )
