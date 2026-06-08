"""
Langfuse 追踪初始化 — 全链路可观测的唯一入口

SDK v3 API:
  lf.start_observation(name, as_type, input) → 创建 trace/span
  span.start_observation(name, as_type, input) → 创建子 span
  span.update(output=...) → 设置输出
  span.end() → 结束

设计原则:
- IS_LANGFUSE_HOST 为空时完全禁用, 零开销
- 启用时设置环境变量, SDK 自动接管
- IS_LANGFUSE_DEBUG_PAYLOAD_SIZE=true 时, OTLP 导出前打印每个 span 的
  input/output 字节数, 用于定位"超时根因是单 span 太大还是 batch 太大"
"""

import logging
import os

from app.config import settings

log = logging.getLogger(__name__)

_enabled: bool = False


def _install_payload_size_probe() -> None:
    """在 OTLPSpanExporter.export 前打印每个 span 的 input/output 字节数.

    诊断 Langfuse OTLP 5s 超时根因 (单 span 过大 vs. batch 过大) 用.
    通过 monkey-patch BatchSpanProcessor 内部的 exporter, 因为 Langfuse
    SDK 把 exporter 嵌在自己的 LangfuseSpanProcessor 里不暴露.
    """
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
    except Exception as e:
        log.warning("[langfuse] payload size probe 安装失败 (OTLP 模块缺失): %s", e)
        return

    _orig_export = OTLPSpanExporter.export
    _INPUT_KEY = "langfuse.observation.input"
    _OUTPUT_KEY = "langfuse.observation.output"

    def _patched_export(self, spans):
        try:
            for sp in spans:
                attrs = getattr(sp, "attributes", {}) or {}
                in_v = attrs.get(_INPUT_KEY, "")
                out_v = attrs.get(_OUTPUT_KEY, "")
                in_sz = len(in_v.encode("utf-8")) if isinstance(in_v, str) else 0
                out_sz = len(out_v.encode("utf-8")) if isinstance(out_v, str) else 0
                attr_total = sum(
                    len(str(v).encode("utf-8", errors="ignore"))
                    for v in attrs.values()
                )
                log.info(
                    "[langfuse-probe] span name=%s input=%dB output=%dB attrs_total=%dB",
                    sp.name, in_sz, out_sz, attr_total,
                )
            log.info("[langfuse-probe] batch export size=%d spans", len(spans))
        except Exception:
            pass  # 探针失败绝不影响真实导出
        return _orig_export(self, spans)

    OTLPSpanExporter.export = _patched_export  # type: ignore[method-assign]
    log.info("[langfuse] payload size probe 已安装")


def init_langfuse():
    """应用启动时调用一次"""
    global _enabled

    if not settings.langfuse_host or not settings.langfuse_public_key:
        log.info("[langfuse] 未配置, 追踪已禁用")
        return

    os.environ["LANGFUSE_HOST"] = settings.langfuse_host
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
    os.environ["LANGFUSE_FLUSH_AT"] = str(settings.langfuse_flush_at)
    os.environ["LANGFUSE_TIMEOUT"] = str(settings.langfuse_timeout)

    if settings.langfuse_debug_payload_size:
        _install_payload_size_probe()

    try:
        from langfuse import Langfuse
        lf = Langfuse()
        if lf.auth_check():
            _enabled = True
            log.info("[langfuse] 追踪已启用 host=%s", settings.langfuse_host)
        else:
            log.warning("[langfuse] 认证失败, 追踪已禁用")
    except Exception as e:
        log.warning("[langfuse] 初始化失败: %s", e)


def get_client():
    """获取 Langfuse 客户端, 未启用时返回 None"""
    if not _enabled:
        return None
    try:
        from langfuse import Langfuse
        return Langfuse()
    except Exception:
        return None


def is_enabled() -> bool:
    return _enabled


def shutdown_langfuse():
    """应用关闭时刷新缓冲区"""
    if not _enabled:
        return
    try:
        from langfuse import Langfuse
        Langfuse().flush()
    except Exception:
        pass
