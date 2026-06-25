"""模型注册中心 — 独立的大模型管理与调用封装.

配置来源与边界（重要）：
- 本 registry 只读取 model_configs 表中的 active config。
- 不 fallback 到 env / settings（IS_LLM_* / IS_EMBEDDING_* 等环境变量由旧链路使用）。
- DB 中无 active config 时，chat_completion() / embed() 抛明确 RuntimeError，不隐式降级。
- 旧链路（llm.py / embedding.py）继续读取 env，与本 registry 并行、互不干扰。
- 首期不自动把 env seed 到 DB；如需初始化，由管理员在模型管理页面手动添加并激活。

设计原则：
- 完全独立，不修改、不替换现有 llm.py / embedding.py
- 从 model_configs 表读取激活配置，在内存中缓存运行时实例
- 对外暴露 chat_completion() / embed() 两个可直接调用的方法

切换策略：
- Chat：支持运行时热切换，新请求即时生效，无需重建任何索引。
- Embedding：仅在首次激活或服务启动时加载，不支持直接热切换。
  切换 Embedding 模型会导致 ChromaDB 旧向量与新查询向量不兼容，
  必须先完成知识库重嵌入（scripts/reembed_after_model_change.py），首期禁止直接切换。

使用示例：
    from app.engine.model_registry import registry

    # Chat 调用
    reply = registry.chat_completion([{"role": "user", "content": "你好"}])

    # Embedding 调用
    vectors = registry.embed(["文本1", "文本2"])

    # 就绪检查
    ready = registry.is_ready()
"""
from __future__ import annotations

import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

_BATCH_SIZE = 10  # Embedding 单次最多条数（与 embedding.py 一致）


class ModelRegistry:
    """大模型注册中心：持有激活配置 + 缓存客户端实例，支持热切换."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 激活配置（从 DB 加载）
        self._chat_config: dict[str, Any] | None = None
        self._embedding_config: dict[str, Any] | None = None
        # 运行时客户端缓存（配置未变时复用，切换时置 None）
        self._chat_client: Any | None = None       # openai.OpenAI
        self._embedding_client: Any | None = None  # openai.OpenAI

    # ── 配置刷新（热切换入口）────────────────────────────────

    def refresh_chat(self, config: dict[str, Any] | None) -> None:
        """更新激活 Chat 配置，清空缓存以触发下次重建."""
        with self._lock:
            self._chat_config = config
            self._chat_client = None
        log.info("[model_registry] Chat 配置已更新: %s",
                 config.get("model_name") if config else "已清空")

    def refresh_embedding(self, config: dict[str, Any] | None) -> None:
        """加载 Embedding 配置（仅用于首次激活或启动恢复，不作为热切换入口）."""
        with self._lock:
            self._embedding_config = config
            self._embedding_client = None
        log.info("[model_registry] Embedding 配置已更新: %s",
                 config.get("model_name") if config else "已清空")

    # ── 就绪状态查询 ─────────────────────────────────────────

    def is_ready(self) -> dict[str, bool]:
        """返回 Chat / Embedding 是否各有激活配置."""
        return {
            "chat_ready": self._chat_config is not None,
            "embedding_ready": self._embedding_config is not None,
            "ready": self._chat_config is not None and self._embedding_config is not None,
        }

    # ── Chat 调用 ─────────────────────────────────────────────

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """使用激活的 Chat 模型发起对话，返回文本内容.

        若无激活配置，抛 RuntimeError 提示先在模型管理中配置。
        temperature / max_tokens 为 None 时使用数据库中配置的值。
        """
        cfg = self._chat_config
        if cfg is None:
            raise RuntimeError(
                "无激活的 Chat 模型配置，请前往「模型管理」页面添加并激活 CHAT 类型配置。"
            )
        proto = cfg.get("protocol", "openai")
        if proto == "anthropic":
            return self._chat_anthropic(cfg, messages, temperature, max_tokens)
        client = self._get_chat_client(cfg)
        resp = client.chat.completions.create(
            model=cfg["model_name"],
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature if temperature is not None else cfg.get("temperature", 0.1),
            max_tokens=max_tokens if max_tokens is not None else cfg.get("max_tokens", 2000),
        )
        return resp.choices[0].message.content or ""

    def _chat_anthropic(
        self,
        cfg: dict[str, Any],
        messages: list[dict[str, str]],
        temperature: float | None,
        max_tokens: int | None,
    ) -> str:
        """使用 Anthropic Messages API 发起对话."""
        client = self._get_chat_client(cfg)
        # 分离 system 消息与 user/assistant 消息
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        kwargs: dict[str, Any] = {
            "model": cfg["model_name"],
            "messages": non_system,  # type: ignore[arg-type]
            "max_tokens": max_tokens if max_tokens is not None else cfg.get("max_tokens", 2000),
        }
        if system_parts:
            kwargs["system"] = "\n".join(system_parts)
        t = temperature if temperature is not None else cfg.get("temperature", 0.1)
        if t is not None:
            kwargs["temperature"] = t
        resp = client.messages.create(**kwargs)
        content = resp.content
        if content and hasattr(content[0], "text"):
            return content[0].text or ""
        return ""

    def _get_chat_client(self, cfg: dict[str, Any]) -> Any:
        """懒加载并缓存 Chat 客户端（线程安全）.

        protocol='anthropic' → anthropic.Anthropic
        protocol='openai'    → openai.OpenAI

        Fix-C: 锁内重读 self._chat_config 而非使用调用方传入的 cfg，
               避免热切换竞态导致用旧 config 构建新 client。
        Fix-A: 构建 base_url 时应用 completions_path 覆盖。
        """
        if self._chat_client is None:
            with self._lock:
                if self._chat_client is None:
                    # 重读以获取最新配置（防止热切换竞态）
                    live = self._chat_config
                    if live is None:
                        raise RuntimeError(
                            "无激活的 Chat 模型配置，"
                            "请前往「模型管理」页面添加并激活 CHAT 类型配置。"
                        )
                    proto = live.get("protocol", "openai")
                    if proto == "anthropic":
                        import anthropic
                        self._chat_client = anthropic.Anthropic(
                            api_key=live["api_key"],
                            base_url=live["base_url"],
                        )
                    else:
                        from openai import OpenAI
                        # 应用 completions_path（非默认路径则拼接到 base_url）
                        base_url = live["base_url"]
                        path = live.get("completions_path") or ""
                        if path and path != "/v1/chat/completions":
                            base_url = base_url.rstrip("/") + path
                        self._chat_client = OpenAI(
                            api_key=live["api_key"],
                            base_url=base_url,
                        )
        return self._chat_client

    # ── Embedding 调用 ────────────────────────────────────────

    def embed(self, texts: list[str]) -> list[list[float]]:
        """使用激活的 Embedding 模型将文本列表向量化.

        返回与输入等长的向量列表（每个向量是 float 列表）。
        若无激活配置，抛 RuntimeError。
        按 _BATCH_SIZE 分批调用，与 embedding.py 保持一致。
        """
        cfg = self._embedding_config
        if cfg is None:
            raise RuntimeError(
                "无激活的 Embedding 模型配置，请前往「模型管理」页面添加并激活 EMBEDDING 类型配置。"
            )
        if cfg.get("protocol", "openai") == "anthropic":
            raise RuntimeError("Anthropic 协议不支持 Embedding 调用")
        if not texts:
            return []

        client = self._get_embedding_client(cfg)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            batch = texts[start: start + _BATCH_SIZE]
            resp = client.embeddings.create(
                model=cfg["model_name"],
                input=batch,
                encoding_format="float",
            )
            ordered = sorted(resp.data, key=lambda d: d.index)
            vectors.extend(item.embedding for item in ordered)
        return vectors

    def _get_embedding_client(self, cfg: dict[str, Any]) -> Any:
        """懒加载并缓存 Embedding OpenAI 客户端（线程安全）.

        Fix-C: 锁内重读 self._embedding_config。
        Fix-A: 构建 base_url 时应用 embeddings_path 覆盖。
        """
        if self._embedding_client is None:
            with self._lock:
                if self._embedding_client is None:
                    live = self._embedding_config
                    if live is None:
                        raise RuntimeError(
                            "无激活的 Embedding 模型配置，"
                            "请前往「模型管理」页面添加并激活 EMBEDDING 类型配置。"
                        )
                    from openai import OpenAI
                    # 应用 embeddings_path（非默认路径则拼接到 base_url）
                    base_url = live["base_url"]
                    path = live.get("embeddings_path") or ""
                    if path and path != "/v1/embeddings":
                        base_url = base_url.rstrip("/") + path
                    self._embedding_client = OpenAI(
                        api_key=live["api_key"],
                        base_url=base_url,
                    )
        return self._embedding_client

    # ── 启动时从 DB 恢复激活配置 ──────────────────────────────

    async def load_from_db(self) -> None:
        """应用启动时调用，从 DB 恢复激活配置到内存（重启后热切换状态不丢失）.

        只加载 model_configs 中 is_active=True 的记录；
        DB 中无 active config 时不报错、不 fallback env，registry 保持未就绪状态。
        """
        try:
            from sqlalchemy import select

            from app.db.metadata import async_session
            from app.models.model_config import ModelConfig

            async with async_session() as db:
                rows = (await db.execute(
                    select(ModelConfig).where(
                        ModelConfig.is_active.is_(True),
                        ModelConfig.is_deleted.is_(False),
                    )
                )).scalars().all()

            for row in rows:
                cfg = self._row_to_dict(row)
                if row.model_type == "CHAT":
                    self.refresh_chat(cfg)
                elif row.model_type == "EMBEDDING":
                    self.refresh_embedding(cfg)

            ready = self.is_ready()
            log.info(
                "[model_registry] 启动加载完成 chat_ready=%s embedding_ready=%s active_count=%d",
                ready["chat_ready"], ready["embedding_ready"], len(rows),
            )
        except Exception as exc:
            log.warning("[model_registry] 启动加载失败（不影响原有 LLM 链路）: %s", exc)

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """ModelConfig ORM 行 → 配置 dict（api_key 由 EncryptedString 透明解密）."""
        return {
            "id": row.id,
            "provider": row.provider,
            "base_url": row.base_url,
            "api_key": row.api_key,        # EncryptedString TypeDecorator 已透明解密
            "model_name": row.model_name,
            "model_type": row.model_type,
            "protocol": row.protocol,
            "temperature": float(row.temperature) if row.temperature is not None else 0.1,
            "max_tokens": row.max_tokens or 2000,
            "completions_path": row.completions_path,
            "embeddings_path": row.embeddings_path,
        }


# ── 进程级单例 ────────────────────────────────────────────────
# 所有调用方 import 此对象即可，无需关心内部状态
registry = ModelRegistry()
