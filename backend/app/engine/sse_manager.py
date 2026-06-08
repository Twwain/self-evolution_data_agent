"""SSE 会话管理器 — per-trace asyncio.Queue 注册表 + SSE 协议格式化.

设计约束:
- 单进程 dict, 不跨 worker 共享 (当前部署单 backend 进程).
- 未来多进程扩展时换 Redis Pub/Sub, 本模块是唯一需改动的点.
"""

import asyncio
import json

# --------------------------------------------------------------------------- #
#  内部注册表                                                                   #
# --------------------------------------------------------------------------- #
_sse_event_queues: dict[str, asyncio.Queue] = {}
_sse_correction_queues: dict[str, asyncio.Queue] = {}


# --------------------------------------------------------------------------- #
#  会话生命周期                                                                 #
# --------------------------------------------------------------------------- #
def register_sse_session(trace_id: str) -> tuple[asyncio.Queue, asyncio.Queue]:
    """创建并注册 per-trace 双队列. 返回 (event_q, correction_q)."""
    if trace_id in _sse_event_queues:
        raise ValueError(f"SSE session {trace_id!r} already registered; deregister first")
    event_q: asyncio.Queue = asyncio.Queue()
    correction_q: asyncio.Queue = asyncio.Queue()
    _sse_event_queues[trace_id] = event_q
    _sse_correction_queues[trace_id] = correction_q
    return event_q, correction_q


def deregister_sse_session(trace_id: str) -> None:
    """移除 trace_id 对应的双队列."""
    _sse_event_queues.pop(trace_id, None)
    _sse_correction_queues.pop(trace_id, None)


# --------------------------------------------------------------------------- #
#  查询接口                                                                     #
# --------------------------------------------------------------------------- #
def get_event_queue(trace_id: str) -> asyncio.Queue | None:
    """返回 event 队列 (backend → frontend), 不存在返回 None."""
    return _sse_event_queues.get(trace_id)


def get_correction_queue(trace_id: str) -> asyncio.Queue | None:
    """返回 correction 队列 (frontend → backend), 不存在返回 None."""
    return _sse_correction_queues.get(trace_id)


def list_active_trace_ids() -> list[str]:
    """返回当前已注册的全部 trace_id 列表."""
    return list(_sse_event_queues.keys())


# --------------------------------------------------------------------------- #
#  SSE 协议格式化                                                               #
# --------------------------------------------------------------------------- #
def format_sse_event(event: str, data: dict | None = None) -> str:
    """SSE 协议格式: 'event: X\\ndata: {...}\\n\\n'.

    非序列化类型 (datetime 等) 通过 default=str 降级为字符串, 不抛异常.
    """
    payload = json.dumps(data or {}, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"
