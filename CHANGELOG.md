# CHANGELOG

All notable changes to this project will be documented in this file.

---

## [Unreleased]

### Added

#### 模型配置管理（Model Management）

- **UI**：新增「模型管理」页面（侧边栏导航），支持 CHAT / EMBEDDING 模型的新增、编辑、删除、激活与连接测试。
- **多协议**：支持 OpenAI 兼容协议与 Anthropic Messages API 协议；`custom` 提供商允许用户手动选择调用协议。
- **Anthropic**：新增 Anthropic 提供商；Anthropic 配置仅支持 CHAT，不支持 EMBEDDING。
- **Embedding 保护**：首期禁止直接切换 active Embedding 配置（会导致 ChromaDB 向量不兼容）；active Embedding 编辑/删除同样受保护，需重建知识库索引后操作。
- **审计日志**：模型配置的新增、更新、删除、激活/停用操作均写入 `model_config_audit_logs` 表，敏感字段（`api_key`、`proxy_password`）在日志中脱敏。
- **API 密钥加密**：`api_key` 和 `proxy_password` 使用 Fernet 对称加密存储（与数据源密码使用相同机制）。
- **热切换**：Chat 模型支持运行时热切换，新请求即时生效。
- **就绪检查**：`/api/model-config/check-ready` 返回 DB active 配置状态，供前端判断模型是否可用。
- **测试**：后端新增 `tests/model_config/` 目录，包含审计、ready 语义、registry 加载的单元测试及 fixture。

### Changed

- 模型配置页面描述文案：明确 Chat 支持热切换、Embedding 切换需重建索引。

### Technical

- 新增 `model_configs` 表（含 `protocol` 字段）及 `model_config_audit_logs` 审计表。
- 新增 `ModelRegistry` 进程级单例（`engine/model_registry.py`），DB 链路与旧 env 链路并行，互不 fallback。
- `engine/llm.py` 新增 `build_chat_client()` 工厂，供 API 层连接测试使用，避免在 API 层散落客户端构造代码。
- 所有 `updated_at` 写入统一使用 `local_now()`（Asia/Shanghai），禁止裸 `datetime.now()`。
