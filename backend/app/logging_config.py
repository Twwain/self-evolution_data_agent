"""
集中日志配置 — 统一格式 + Console/File 双输出
每次启动生成独立日志文件: app-20260408-170512.log
"""

import logging
import os
from contextvars import ContextVar
from datetime import datetime

# ══════════════════════════════════════════════════════════════
#  Trace ID 关联 — 与 Langfuse 共用 trace_id 打通日志与 LLM 链路
# ══════════════════════════════════════════════════════════════
# 业务 trace_id (uuid4): FastAPI middleware / agent_loop 写入,
#   是 session_id (跨多轮请求复用), 用作 cancel / SSE / DB FK.
# Langfuse trace_id (OTel SpanContext, 32 hex): 每次 HTTP 请求一个,
#   middleware 起 root span 时由 OTel SDK 生成, 通过 OTel API 直读.
#
# 双 ID 共存策略: logger 同时打两列, 业务 ID 串多轮会话, langfuse ID
# 直接跳到 langfuse UI 看本次 LLM 链路细节, 互不替代.
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


def _current_lf_trace_id() -> str:
    """从 OTel current span 取 langfuse trace_id (32 hex). 无活跃 span 返 '-'."""
    try:
        from opentelemetry import trace as otel_trace
        ctx = otel_trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return "-"
        return format(ctx.trace_id, "032x")
    except Exception:
        return "-"


class TraceIdFilter(logging.Filter):
    """将 contextvar 中的 trace_id 注入到 LogRecord, 供 formatter 引用"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_var.get()
        record.lf_trace_id = _current_lf_trace_id()
        return True


# ── 格式: 2026-04-07 15:05:18 | trace=abc123 | lf=35f492d1... | INFO | trainer | 开始训练 ──
LOG_FORMAT = (
    "%(asctime)s | trace=%(trace_id)s | lf=%(lf_trace_id)s "
    "| %(levelname)-5s | %(name)s | %(message)s"
)
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ── 文件输出配置 ──
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "logs")
MAX_LOG_FILES = 10  # 保留最近 10 次启动的日志


def _cleanup_old_logs():
    """保留最近 MAX_LOG_FILES 个日志文件, 删除更早的"""
    try:
        files = sorted(
            (f for f in os.listdir(LOG_DIR) if f.startswith("app-") and f.endswith(".log")),
            reverse=True,
        )
        for old in files[MAX_LOG_FILES:]:
            os.remove(os.path.join(LOG_DIR, old))
    except OSError:
        pass


def setup_logging(level: int = logging.INFO) -> None:
    """
    初始化根 logger: console + per-startup file
    每次启动生成 app-{timestamp}.log, 自动清理旧文件
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    trace_filter = TraceIdFilter()

    # ── Console handler ──
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    console.addFilter(trace_filter)

    # ── Per-startup file handler ──
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_filename = f"app-{timestamp}.log"
    file_path = os.path.join(LOG_DIR, log_filename)
    file_handler = logging.FileHandler(file_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(trace_filter)

    # ── 配置根 logger ──
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)

    # 降低第三方库噪音
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)

    _cleanup_old_logs()
    logging.getLogger("main").info("日志文件: %s", file_path)


def get_logger(name: str) -> logging.Logger:
    """获取命名 logger, 继承根配置"""
    return logging.getLogger(name)
