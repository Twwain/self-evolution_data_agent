"""Stage 4 Task 10 — agent loop runtime context binding for tool registry.

Tool callables in REGISTRY take **kwargs from LLM (collection, query, fields, etc.).
Runtime context (db, namespace_id, ns_slug, trace_id) must be bound by dispatcher
before invocation — these are NOT LLM-visible.

设计要点:
- inspect 每个 tool 签名, 仅注入 fn 接收的 ctx kwarg, 不接收的丢弃, 避 TypeError.
- LLM 给的 kwargs 优先级高于 ctx (LLM 不应传, 防御性 merge).
- 同时支持 async / sync tool — recommend_chart_tool 是同步, 用 inspect.isawaitable 兼容.
- datasource_id 不再注入: mongo 工具按 (ns_id, database) 反查 ds, 不绑死单 ds.
"""
from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.tools import registry as registry_mod

log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  公开入口
# ════════════════════════════════════════════

def build_bound_registry(
    *,
    db: AsyncSession,
    namespace_id: int,
    ns_slug: str,
    trace_id: str,
    sse_emit: Callable[[dict], Awaitable[None]],
) -> dict[str, Callable[..., Awaitable[Any]]]:
    """Wrap each tool in REGISTRY to inject runtime context.

    Returns: name → bound async callable that takes only LLM-visible kwargs.
    Inspects each tool's signature; binds only the matching runtime kwargs.

    sse_emit 注入 (P0-3): 工具如需推送中间过程事件 (clarify_request / knowledge_proposed
    / cost_warning), 在签名加 sse_emit kwarg, dispatcher 自动注入. 不接收的 tool 不受影响.

    NOTE: 取 registry_mod.REGISTRY (而非 import 时绑死), 便于测试 monkeypatch.
    """
    runtime_ctx: dict[str, Any] = {
        "db": db,
        "namespace_id": namespace_id,
        "ns_slug": ns_slug,
        "trace_id": trace_id,
        "sse_emit": sse_emit,
    }
    bound: dict[str, Callable[..., Awaitable[Any]]] = {}
    for name, fn in registry_mod.REGISTRY.items():
        bound[name] = _bind_one(fn, runtime_ctx)
    return bound


# ════════════════════════════════════════════
#  内部: 单个 tool 包装
# ════════════════════════════════════════════

def _bind_one(
    fn: Callable, runtime_ctx: dict[str, Any],
) -> Callable[..., Awaitable[Any]]:
    """Inspect fn signature; build wrapper that injects only matching ctx kwargs.

    - 仅当 fn 显式声明对应参数 (或 **kwargs) 才注入 ctx 值.
    - None ctx 值跳过.
    - LLM 已传的 kwarg 不被覆盖 (defensive, LLM 不应该传 ctx 字段).
    - 并发安全: 如果 fn 需要 db, 创建独立 session 避免 SQLAlchemy 并发冲突.
    """
    sig = inspect.signature(fn)
    has_var_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    accepted_ctx_keys = [
        k for k in runtime_ctx
        if has_var_kwargs or k in sig.parameters
    ]
    needs_db = 'db' in accepted_ctx_keys

    async def _wrapped(**llm_kwargs: Any) -> Any:
        # 如果工具需要 db, 创建独立 session (并发安全)
        if needs_db:
            from app.db.metadata import async_session
            async with async_session() as db:
                # 构造 ctx, 用独立 session 替换原来的 db
                ctx = {}
                for k in accepted_ctx_keys:
                    if k == 'db':
                        ctx['db'] = db
                    elif runtime_ctx[k] is not None:
                        ctx[k] = runtime_ctx[k]

                # LLM kwargs override ctx (defensive — LLM shouldn't pass these)
                merged = {**ctx, **llm_kwargs}
                result = fn(**merged)
                if inspect.isawaitable(result):
                    return await result
                return result
        else:
            # 不需要 db 的工具, 直接使用原有逻辑
            ctx = {
                k: runtime_ctx[k] for k in accepted_ctx_keys
                if runtime_ctx[k] is not None
            }
            merged = {**ctx, **llm_kwargs}
            result = fn(**merged)
            if inspect.isawaitable(result):
                return await result
            return result

    return _wrapped
