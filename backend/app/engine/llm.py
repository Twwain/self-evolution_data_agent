"""
LLM 统一抽象层 — 所有 LLM 调用的唯一入口
消灭散落各处的 OpenAI() 实例化, 一个函数路由一切

设计哲学:
    不用类继承, 不搞策略模式 — 一个函数 + 一个 if 就够了
    过度抽象是简单问题的复杂化, 两个 provider 不值得一个工厂

追踪:
    Langfuse 启用时, chat_completion / chat_completion_checked 自动记录
    generation 观测 (model/input/output/usage). 未启用时 @observe 变成 no-op,
    调用方无需感知. 所有下游 LLM 调用都必然经过这两个入口, 覆盖全链路.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import anthropic
import openai
from langfuse import observe
from openai import OpenAI

from app.config import settings
from app.engine.json_parser import parse_llm_json
from app.tracing import get_client as _lf_client

logger = logging.getLogger(__name__)


class EmptyLLMResponseError(RuntimeError):
    """LLM returned empty/null content — retryable transient error."""
    pass

# ── Bedrock proxy tool_use_id 合规校验 ──────────────────────────────────────
# Bedrock proxy 对 tool_use_id 强制 ^[a-zA-Z0-9_-]+$ 校验;
# Anthropic 官方 toolu_xxx 本身合规, 某些 proxy 路径会 mangle 前缀致 422.
_TOOL_ID_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")


def _sanitize_tool_use_id(raw_id: str) -> str:
    """清洗 tool_use_id, 确保符合 Bedrock proxy ^[a-zA-Z0-9_-]+$ 约束."""
    if not raw_id:
        return "tool_id_unknown"
    return _TOOL_ID_UNSAFE.sub("_", raw_id)


# ── 懒初始化的客户端单例 ──
_openai_client: OpenAI | None = None
_claude_client: anthropic.Anthropic | None = None


def _get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    return _openai_client


def _get_claude_client() -> anthropic.Anthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.Anthropic(
            api_key=settings.claude_api_key,
            base_url=settings.claude_base_url,
        )
    return _claude_client


# ════════════════════════════════════════════
#  Langfuse generation 元数据回填
#  — 在 @observe 上下文内安全调用, 无 trace 时 get_client() 返回 None
# ════════════════════════════════════════════

def _last_input_block(messages: list[dict]) -> list[dict]:
    """仅取本轮 generation 的"新增输入" — 首条 system + 最后一段非 assistant 消息.

    背景: agent_loop 多轮迭代每轮把累积 messages 全量塞进 generation span 的 input,
    第 12 轮已 = system + 12×(assistant + N tool_result), payload 易破 MB, 触发
    OTLP 5s 超时. langfuse trace 视图能从父 chain 串起每轮 generation, 单轮 generation
    只需自描述本轮输入即可, 历史上下文是冗余.

    规则:
      - 首条若为 system, 保留 (定位 agent persona)
      - 从尾向前扫, 收集"最后一个 assistant 之后的所有消息" (即本轮真正的新增 input)
      - 若整列无 assistant, 退化为最后一条 user 消息
    """
    if not messages:
        return []
    head: list[dict] = []
    if messages[0].get("role") == "system":
        head.append(messages[0])

    last_asst = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            last_asst = i
            break
    tail = messages[last_asst + 1:] if last_asst >= 0 else messages[-1:]
    return head + tail


def _record_generation(
    *, model: str, messages: list[dict], output: str | dict,
    input_tokens: int | None = None, output_tokens: int | None = None,
    tools: list[dict] | None = None,
) -> None:
    lf = _lf_client()
    if lf is None:
        return
    try:
        usage: dict = {}
        if input_tokens is not None:
            usage["input"] = input_tokens
        if output_tokens is not None:
            usage["output"] = output_tokens
        if input_tokens is not None and output_tokens is not None:
            usage["total"] = input_tokens + output_tokens
        metadata = {"tools": tools} if tools else None
        lf.update_current_generation(
            model=model,
            input=_last_input_block(messages),
            output=output,
            usage_details=usage or None,
            metadata=metadata,
        )
    except Exception:
        pass


# ════════════════════════════════════════════
#  P1-14: LLM transient 错误分类
#  5xx / Timeout / Connection 重试; 4xx 不重试 (业务错误)
# ════════════════════════════════════════════

def _is_transient_llm_error(exc: BaseException) -> bool:
    """判断异常是否 transient (网络抖动 / 5xx / Timeout / 限流 / 空响应), 应重试.

    4xx (bad_request / auth_error 等业务错误) 不重试.
    """
    if isinstance(exc, EmptyLLMResponseError):
        return True

    # ── OpenAI 兼容 (默认线路: 任何 OpenAI-shaped 端点) ──
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code >= 500

    # ── Anthropic (Claude) ──
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code >= 500

    return False


# ════════════════════════════════════════════
#  主接口 — 每次调用落一个 generation 观测
# ════════════════════════════════════════════

@observe(as_type="generation", name="chat_completion", capture_input=False, capture_output=False)
def chat_completion(
    messages: list[dict],
    temperature: float = 0.1,
    max_tokens: int = 12288,  # noqa: hardcode
    provider: str | None = None,
    extra_body: dict | None = None,
) -> str:
    """
    统一聊天补全接口
    messages 格式统一用 OpenAI 风格: [{"role": "system|user|assistant", "content": "..."}]
    内部自动适配不同 provider 的 API 差异
    本函数被 @observe 包装, 自动落 generation 观测 (无 trace 时变 no-op)

    extra_body: OpenAI 兼容端点的厂商扩展参数 (如 DeepSeek 的
        {"thinking": {"type": "disabled"}} 关闭思考模式). 仅 openai 路径透传,
        anthropic 路径忽略 (Claude 思考默认关闭, 由 thinking 参数单独控制).
    """
    provider = provider or settings.llm_provider

    if provider == "anthropic":
        return _claude_chat_with_retry(messages, temperature, max_tokens)
    return _openai_chat_with_retry(messages, temperature, max_tokens, extra_body=extra_body)


# ════════════════════════════════════════════
#  OpenAI — OpenAI Chat Completions 协议, 直通
#  (DashScope / DeepSeek / vLLM / 官方 OpenAI 等任意兼容端点)
# ════════════════════════════════════════════

def _openai_chat(messages: list[dict], temperature: float, max_tokens: int,
                 extra_body: dict | None = None) -> str:
    client = _get_openai_client()
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
        extra_body=extra_body,
    )
    text = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    if not text.strip():
        # Record to Langfuse BEFORE raising — preserves input for diagnostics
        _record_generation(
            model=settings.llm_model, messages=messages, output="[EMPTY_RESPONSE]",
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
        )
        raise EmptyLLMResponseError(
            f"OpenAI-compatible endpoint returned empty content (model={settings.llm_model}, "
            f"finish_reason={resp.choices[0].finish_reason})"
        )
    _record_generation(
        model=settings.llm_model, messages=messages, output=text,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
    )
    return text


def _openai_chat_with_retry(messages: list[dict], temperature: float, max_tokens: int,
                            extra_body: dict | None = None) -> str:
    """OpenAI-compatible chat + transient-error retry (指数退避, 上限 llm_retry_max)."""
    last_exc: BaseException | None = None
    for attempt in range(settings.llm_retry_max + 1):
        try:
            return _openai_chat(messages, temperature, max_tokens, extra_body=extra_body)
        except Exception as e:
            if not _is_transient_llm_error(e) or attempt == settings.llm_retry_max:
                raise
            last_exc = e
            wait_secs = min(2 ** attempt, 10)
            logger.warning(
                "openai transient error retry %d/%d after %ds: %s",
                attempt + 1, settings.llm_retry_max, wait_secs, e,
            )
            time.sleep(wait_secs)
    raise last_exc  # type: ignore[misc]  # unreachable


# ════════════════════════════════════════════
#  Claude content 解析 — 无差别提取 TextBlock
#  ── 不干预 thinking 决策, 但保证拿得到结果.
#     · [TextBlock]                 → 取 text
#     · [ThinkingBlock, TextBlock]  → 取 text (忽略 thinking)
#     · [TextBlock, ToolUse, ...]   → 拼接全部 text
#     · [ThinkingBlock]             → 返回空, 上层判空触发降级重试
# ════════════════════════════════════════════

def _extract_claude_text(content) -> str:
    return "".join(
        b.text for b in (content or [])
        if getattr(b, "type", None) == "text" and hasattr(b, "text")
    )


def _block_types(content) -> list[str]:
    return [getattr(b, "type", type(b).__name__) for b in (content or [])]


# ════════════════════════════════════════════
#  Claude — Anthropic API, 需要适配 message 格式
#  核心差异: system 不在 messages 里, 是独立参数
# ════════════════════════════════════════════

def _claude_chat(messages: list[dict], temperature: float, max_tokens: int) -> str:
    client = _get_claude_client()

    # 提取 system message (Claude API 要求独立传)
    system_text = ""
    user_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text += msg["content"] + "\n"
        else:
            user_messages.append({"role": msg["role"], "content": msg["content"]})

    # Claude 要求第一条必须是 user
    if not user_messages or user_messages[0]["role"] != "user":
        user_messages.insert(0, {"role": "user", "content": "请根据以上要求回答。"})
    system_param = system_text.strip() or None

    # 大 max_tokens 必须走流式, 否则 SDK 预估超 10min 会直接拒绝
    # SDK 公式: expected_time = 3600 × max_tokens / 128000, > 600s 即 raise
    # 解出真实临界 21333, 留 buffer 取 21000
    if max_tokens > 21000:
        text_parts: list[str] = []
        input_tokens: int | None = None
        output_tokens: int | None = None
        final = None
        with client.messages.stream(
            model=settings.claude_model,
            system=system_param,  # type: ignore[arg-type]
            messages=user_messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
        ) as stream:
            for text in stream.text_stream:
                text_parts.append(text)
            final = stream.get_final_message()
            if final and final.usage:
                input_tokens = final.usage.input_tokens
                output_tokens = final.usage.output_tokens
        result = "".join(text_parts)
        if not result:
            blocks = _block_types(final.content) if final else []
            stop = final.stop_reason if final else None
            thinking_preview = ""
            for b in (final.content if final else []) or []:
                if getattr(b, "type", None) == "thinking":
                    t = getattr(b, "thinking", "") or ""
                    thinking_preview = t[:500]
                    break
            diag = (
                f"<EMPTY_TEXT> blocks={blocks} stop_reason={stop} "
                f"in_tok={input_tokens} out_tok={output_tokens} "
                f"thinking_preview={thinking_preview!r}"
            )
            logger.warning("Claude 流式返回空文本: %s", diag)
            _record_generation(
                model=settings.claude_model, messages=messages, output=diag,
                input_tokens=input_tokens, output_tokens=output_tokens,
            )
            raise EmptyLLMResponseError(
                f"Claude 流式返回空文本 blocks={blocks} stop_reason={stop} "
                f"out_tok={output_tokens}"
            )
        _record_generation(
            model=settings.claude_model, messages=messages, output=result,
            input_tokens=input_tokens, output_tokens=output_tokens,
        )
        return result

    # ── 首次: 不干预 thinking, 接受任意 block 组合 ──
    resp = client.messages.create(
        model=settings.claude_model,
        system=system_param,  # type: ignore[arg-type]
        messages=user_messages,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
    )
    text = _extract_claude_text(resp.content)

    # ── 空文本 → 假设服务端偶发抖动, 原参数重试一次 ──
    if not text:
        blocks1 = _block_types(resp.content)
        stop1 = resp.stop_reason
        logger.warning(
            "Claude 首次空文本 blocks=%s stop=%s — 原参数重试", blocks1, stop1,
        )
        resp = client.messages.create(
            model=settings.claude_model,
            system=system_param,  # type: ignore[arg-type]
            messages=user_messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = _extract_claude_text(resp.content)
        if not text:
            blocks2 = _block_types(resp.content)
            stop2 = resp.stop_reason
            diag = (
                f"<EMPTY_TEXT> first=(blocks={blocks1},stop={stop1}) "
                f"retry=(blocks={blocks2},stop={stop2})"
            )
            _record_generation(
                model=settings.claude_model, messages=messages, output=diag,
                input_tokens=resp.usage.input_tokens if resp.usage else None,
                output_tokens=resp.usage.output_tokens if resp.usage else None,
            )
            raise EmptyLLMResponseError(
                f"Claude 两次调用均无 TextBlock "
                f"first=(blocks={blocks1},stop={stop1}) "
                f"retry=(blocks={blocks2},stop={stop2})"
            )

    _record_generation(
        model=settings.claude_model, messages=messages, output=text,
        input_tokens=resp.usage.input_tokens if resp.usage else None,
        output_tokens=resp.usage.output_tokens if resp.usage else None,
    )
    return text


def _claude_chat_with_retry(messages: list[dict], temperature: float, max_tokens: int) -> str:
    """Claude chat with transient-error retry (指数退避, 最多 settings.llm_retry_max 次重试)."""
    last_exc: BaseException | None = None
    for attempt in range(settings.llm_retry_max + 1):
        try:
            return _claude_chat(messages, temperature, max_tokens)
        except Exception as e:
            if not _is_transient_llm_error(e) or attempt == settings.llm_retry_max:
                raise
            last_exc = e
            wait_secs = min(2 ** attempt, 10)
            logger.warning(
                "claude transient error retry %d/%d after %ds: %s",
                attempt + 1, settings.llm_retry_max, wait_secs, e,
            )
            time.sleep(wait_secs)
    raise last_exc  # type: ignore[misc]  # unreachable


# ════════════════════════════════════════════
#  带截断检测的接口 — 零侵入, 新增不改旧
# ════════════════════════════════════════════

class LLMResponse:
    """LLM 响应 + 截断元数据"""
    __slots__ = ("text", "truncated")

    def __init__(self, text: str, truncated: bool):
        self.text = text
        self.truncated = truncated


@observe(as_type="generation", name="chat_completion_checked", capture_input=False, capture_output=False)
def chat_completion_checked(
    messages: list[dict],
    temperature: float = 0.1,
    max_tokens: int = 12288,  # noqa: hardcode
    provider: str | None = None,
) -> LLMResponse:
    """同 chat_completion, 但额外返回截断状态 (finish_reason == length/max_tokens)"""
    provider = provider or settings.llm_provider

    if provider == "anthropic":
        return _claude_chat_checked(messages, temperature, max_tokens)
    return _openai_chat_checked(messages, temperature, max_tokens)


def _openai_chat_checked(messages: list[dict], temperature: float, max_tokens: int) -> LLMResponse:
    client = _get_openai_client()
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
    )
    truncated = resp.choices[0].finish_reason == "length"
    text = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    _record_generation(
        model=settings.llm_model, messages=messages, output=text,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
    )
    return LLMResponse(text, truncated)


def _claude_chat_checked(messages: list[dict], temperature: float, max_tokens: int) -> LLMResponse:
    client = _get_claude_client()

    system_text = ""
    user_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text += msg["content"] + "\n"
        else:
            user_messages.append({"role": msg["role"], "content": msg["content"]})

    if not user_messages or user_messages[0]["role"] != "user":
        user_messages.insert(0, {"role": "user", "content": "请根据以上要求回答。"})
    system_param = system_text.strip() or None

    # ── 首次: 不干预 thinking ──
    resp = client.messages.create(
        model=settings.claude_model,
        system=system_param,  # type: ignore[arg-type]
        messages=user_messages,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
    )
    text = _extract_claude_text(resp.content)

    # ── 空文本 → 假设服务端偶发抖动, 原参数重试一次 ──
    if not text:
        blocks1 = _block_types(resp.content)
        stop1 = resp.stop_reason
        logger.warning(
            "Claude checked 首次空文本 blocks=%s stop=%s — 原参数重试", blocks1, stop1,
        )
        resp = client.messages.create(
            model=settings.claude_model,
            system=system_param,  # type: ignore[arg-type]
            messages=user_messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = _extract_claude_text(resp.content)
        if not text:
            blocks2 = _block_types(resp.content)
            stop2 = resp.stop_reason
            diag = (
                f"<EMPTY_TEXT> first=(blocks={blocks1},stop={stop1}) "
                f"retry=(blocks={blocks2},stop={stop2})"
            )
            _record_generation(
                model=settings.claude_model, messages=messages, output=diag,
                input_tokens=resp.usage.input_tokens if resp.usage else None,
                output_tokens=resp.usage.output_tokens if resp.usage else None,
            )
            raise EmptyLLMResponseError(
                f"Claude checked 两次调用均无 TextBlock "
                f"first=(blocks={blocks1},stop={stop1}) "
                f"retry=(blocks={blocks2},stop={stop2})"
            )

    truncated = resp.stop_reason == "max_tokens"
    _record_generation(
        model=settings.claude_model, messages=messages, output=text,
        input_tokens=resp.usage.input_tokens if resp.usage else None,
        output_tokens=resp.usage.output_tokens if resp.usage else None,
    )
    return LLMResponse(text, truncated)


# ════════════════════════════════════════════
#  Stage 4 Task 1 — Tool-use 适配层
#  中性 tool spec → provider 转换 → ToolUseResponse
# ════════════════════════════════════════════


@dataclass
class ToolCall:
    id: str           # tool_call_id, 用于结果回喂时与 tool_result 配对
    name: str         # tool 名
    input: dict       # tool 参数 (已 JSON 解码)


@dataclass
class ToolUseResponse:
    text: str                       # LLM 文本回复 (可能为空)
    tool_calls: list[ToolCall]
    stop_reason: str                # "tool_use" | "end_turn" | "max_tokens" | "stop" | "tool_calls"
    usage: dict = field(default_factory=dict)


@observe(as_type="generation", name="chat_completion_with_tools", capture_input=False, capture_output=False)
async def chat_completion_with_tools(
    messages: list[dict],
    tools: list[dict],
    provider: str | None = None,
    stream_callback: Callable[[dict], Awaitable[None]] | None = None,
    temperature: float = 0.1,
    max_tokens: int = 12288,  # noqa: hardcode
) -> ToolUseResponse:
    """统一 tool_use 入口.

    中性 tool spec 格式 (与 OpenAI / Anthropic 都兼容):
        {"name": str, "description": str, "input_schema": {json schema}}

    内部按 provider 分流:
        - anthropic → Anthropic tool_use block (Claude)
        - openai → OpenAI Chat Completions function calling (DashScope / DeepSeek / vLLM / …)
    stream_callback 在 Stage 5 SSE 接入, 当前轮次完整返回, 不分块推送.
    """
    provider = provider or settings.llm_provider
    if provider == "anthropic":
        resp = await asyncio.to_thread(
            _claude_tool_use, messages, tools, temperature, max_tokens,
        )
    else:
        resp = await asyncio.to_thread(
            _openai_tool_use, messages, tools, temperature, max_tokens,
        )
    _coerce_tool_call_args(resp.tool_calls, tools)
    return resp


# ── OpenAI-compatible (Chat Completions function calling) ──

def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """把中性消息格式适配为 OpenAI Chat Completions 线格式.

    中性 assistant 消息 (agent_loop 产出):
        {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "input"}]}
    OpenAI 线格式要求每个 tool_call 为:
        {"id", "type": "function", "function": {"name", "arguments": "<json str>"}}

    system / user / tool 消息本就符合 OpenAI, 原样透传.
    """
    converted: list[dict] = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            converted.append({
                "role": "assistant",
                "content": m.get("content") or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["input"], ensure_ascii=False),
                        },
                    }
                    for tc in m["tool_calls"]
                ],
            })
        else:
            converted.append(m)
    return converted


def _openai_tool_use(
    messages: list[dict], tools: list[dict],
    temperature: float, max_tokens: int,
) -> ToolUseResponse:
    client = _get_openai_client()
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=_to_openai_messages(messages),  # type: ignore[arg-type]
        tools=openai_tools,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
    )
    choice = resp.choices[0]
    msg = choice.message
    text = msg.content or ""
    raw_calls = getattr(msg, "tool_calls", None) or []
    tool_calls = [
        ToolCall(
            id=tc.id,
            name=tc.function.name,
            input=_safe_json_loads(tc.function.arguments),
        )
        for tc in raw_calls
    ]
    usage = getattr(resp, "usage", None)
    usage_dict = {
        "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }
    _record_generation(
        model=settings.llm_model, messages=messages,
        output={"text": text, "tool_calls": [tc.__dict__ for tc in tool_calls]},
        input_tokens=usage_dict["input_tokens"], output_tokens=usage_dict["output_tokens"],
        tools=openai_tools,
    )
    return ToolUseResponse(
        text=text,
        tool_calls=tool_calls,
        stop_reason=choice.finish_reason or "stop",
        usage=usage_dict,
    )


# ── Claude (Anthropic native tool_use blocks) ──

def _claude_tool_use(
    messages: list[dict], tools: list[dict],
    temperature: float, max_tokens: int,
) -> ToolUseResponse:
    client = _get_claude_client()

    system_text = ""
    user_messages: list[dict] = []
    tool_results_buffer: list[dict] = []  # 缓存连续的 tool results

    for m in messages:
        if m["role"] == "system":
            system_text += m["content"] + "\n"
        elif m["role"] == "tool":
            # 收集 tool result 到 buffer
            tool_results_buffer.append({
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m["content"],
            })
        elif m["role"] == "assistant":
            # 遇到 assistant 消息，先 flush tool results buffer
            if tool_results_buffer:
                user_messages.append({
                    "role": "user",
                    "content": tool_results_buffer.copy()
                })
                tool_results_buffer.clear()

            # 转换 assistant 消息：OpenAI 格式 → Claude 格式
            # OpenAI: {"role": "assistant", "content": str, "tool_calls": [...]}
            # Claude:  {"role": "assistant", "content": [{"type": "text", ...}, {"type": "tool_use", ...}]}
            content_blocks = []
            if m.get("content"):
                content_blocks.append({"type": "text", "text": m["content"]})

            for tc in m.get("tool_calls", []):
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })

            user_messages.append({
                "role": "assistant",
                "content": content_blocks
            })
        else:
            # 其他消息（user），先 flush buffer
            if tool_results_buffer:
                user_messages.append({
                    "role": "user",
                    "content": tool_results_buffer.copy()
                })
                tool_results_buffer.clear()
            user_messages.append(m)

    # 最后 flush 剩余的 tool results
    if tool_results_buffer:
        user_messages.append({
            "role": "user",
            "content": tool_results_buffer.copy()
        })

    if not user_messages or user_messages[0]["role"] != "user":
        user_messages.insert(0, {"role": "user", "content": "请根据以上要求回答。"})
    system_param = system_text.strip() or anthropic.NOT_GIVEN

    # Claude 的 input_schema 字段名与中性 spec 一致, 直接透传
    claude_tools = [
        {"name": t["name"], "description": t.get("description", ""),
         "input_schema": t["input_schema"]}
        for t in tools
    ]

    resp = client.messages.create(
        model=settings.claude_model,
        system=system_param,  # type: ignore[arg-type]
        messages=user_messages,  # type: ignore[arg-type]
        tools=claude_tools,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
    )

    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in resp.content or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif btype == "tool_use":
            raw_id = getattr(block, "id", "") or ""
            tool_calls.append(ToolCall(
                id=_sanitize_tool_use_id(raw_id),
                name=getattr(block, "name"),
                input=dict(getattr(block, "input", {}) or {}),
            ))

    text = "".join(text_parts)
    usage_dict = {
        "input_tokens": resp.usage.input_tokens if resp.usage else 0,
        "output_tokens": resp.usage.output_tokens if resp.usage else 0,
    }
    _record_generation(
        model=settings.claude_model, messages=messages,
        output={"text": text, "tool_calls": [tc.__dict__ for tc in tool_calls]},
        input_tokens=usage_dict["input_tokens"], output_tokens=usage_dict["output_tokens"],
        tools=claude_tools,
    )
    return ToolUseResponse(
        text=text,
        tool_calls=tool_calls,
        stop_reason=resp.stop_reason or "end_turn",
        usage=usage_dict,
    )


def _safe_json_loads(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        logger.warning("OpenAI-compatible tool arguments not valid JSON: %r", raw[:200])
        return {}


def _coerce_tool_call_args(tool_calls: list[ToolCall], tools: list[dict]) -> None:
    """还原被 provider 适配层拍平成字符串的嵌套 object/array 入参 (就地修改).

    部分 Anthropic-兼容代理 (如 DeepSeek modelproxy) 会把 tool_use 入参中声明为
    object/array 的嵌套字段拍平成 JSON 字符串, 导致下游 `query.get(...)` 等在
    str 上调用 dict 方法报 `'str' object has no attribute 'get'`. 这里按 tool spec
    的 input_schema 声明类型, 把本应是 object/array 却收到 str 的字段用统一解析器
    还原. 解析失败保持原值, 交给后续自然报错, 不掩盖真正畸形的输入.
    """
    prop_types: dict[str, dict[str, str | None]] = {}
    for spec in tools:
        name = spec.get("name")
        props = (spec.get("input_schema") or {}).get("properties") or {}
        if name:
            prop_types[name] = {
                k: v.get("type") for k, v in props.items() if isinstance(v, dict)
            }

    for tc in tool_calls:
        types = prop_types.get(tc.name)
        if not types or not isinstance(tc.input, dict):
            continue
        for key, value in tc.input.items():
            if not isinstance(value, str):
                continue
            declared = types.get(key)
            if declared not in ("object", "array"):
                continue
            parsed = parse_llm_json(value, expect="dict" if declared == "object" else "list")
            if parsed is not None:
                tc.input[key] = parsed

