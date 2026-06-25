# backend/app/engine — 引擎层

大模型调用、Agent 循环、向量检索等核心引擎。

## 文件清单

| 文件 | 说明 |
|---|---|
| `llm.py` | Chat LLM 调用封装（settings 链路）；`build_chat_client(api_key, base_url, protocol)` 为不读 settings 的临时客户端工厂 |
| `embedding.py` | Embedding 向量化（settings 链路） |
| `model_registry.py` | **DB 链路**模型注册中心；进程级单例，持有 DB active config + 懒加载客户端缓存；支持 Chat 热切换，Embedding 首期禁止切换 |
| `agent_loop.py` | Agent 主循环 |
| `agent_loop_dispatcher.py` | Agent 任务分发 |
| `plan_generator.py` | 执行计划生成 |
| `plan_executor.py` | 执行计划运行 |
| `plan_models.py` | 执行计划数据模型 |
| `recall_window.py` | 上下文召回窗口 |
| `pending_cleanup.py` | 悬挂任务清理 |
| `registry.py` | 工具/能力注册表 |
| `repo_worker.py` | Git 仓库处理 |
| `sse_manager.py` | SSE 推送管理 |
| `visualizer.py` | 可视化辅助 |
| `json_parser.py` | JSON 解析工具 |
| `db_types.py` | DB 类型声明 |

## 关键边界

- `llm.py` / `embedding.py`：**只读 env / settings**，不读 `model_configs` DB。
- `model_registry.py`：**只读 DB active config**，不 fallback env。两者并行，互不干扰。
- `build_chat_client()` 是唯一被允许在 API 层调用的客户端工厂，用于连接测试等一次性场景。API 层禁止直接 `from openai import OpenAI` / `anthropic.Anthropic(...)` 构造客户端。
