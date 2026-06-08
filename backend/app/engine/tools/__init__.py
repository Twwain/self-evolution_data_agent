"""Stage 4 — Agent loop tool wrappers.

命名约定: async def tool_name(..., kwargs) -> dict (JSON-safe).

事务契约:
  - 写操作 tool (save_knowledge 等) 仅 db.add + db.flush, 不 commit.
  - 事务提交由调用方 (agent loop 或 API endpoint) 管, 保证多 tool 一轮原子性.

每个 tool 入 registry 后被 agent loop 并发调度.
错误包装在 agent_loop._exec_tool 内, tool 自身可直接 raise.
每个 tool 套 @observe(name="tool.<name>") 让 langfuse 落 span (G2).
"""
