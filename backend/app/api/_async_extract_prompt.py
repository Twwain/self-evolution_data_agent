"""end_turn 后台 LLM-as-extractor 用的 prompt.

按"Prompt 产品化审计 Checklist" (项目根 CLAUDE.md):
- 不含具体客户领域词
- 例子统一用通用电商域 (用户 / 订单 / 商品 / 类目 / 金额)

哲学: LLM 只做不可机械化的语义改写两件事; 机械字段全部由代码侧抽取保真.
"""
from __future__ import annotations

ASYNC_EXTRACT_PROMPT = """你是查询知识沉淀编辑. 一次完整的查询刚结束, 请只完成两个语义任务.

【上下文】
用户问题: {question}
涉及集合 (来自实际 tool 调用, 不可修改): {collections}
tool_trace 摘要 (按时间序):
{tool_trace_summary}

【任务 1 — 骨架重写 (question_pattern)】
把用户原问题改写为可复用骨架, 给未来"像这类问题"做检索:
- 删: 一次性具体值 (ID / 记录名 / 数字 / 日期) → 替换成 "某<集合业务名>" / "某时段" / "若干"
- 保: 业务名词、聚合动作 (统计/分组/排序/过滤)、修饰关系

举例 (仅示范骨架重写风格, 不代表实际业务域):
  原问题: "黄金会员 (ID: 5f8a1b2c) 在 2025 年 3 月下的所有订单, 按商品类目分组统计金额"
  question_pattern: "某用户等级在某时段下的所有订单, 按商品类目分组统计金额"

【任务 2 — 路径理由 (route_hint_reason)】
仅当涉及集合数 >= 2 时填. 用一句 ≤30 字概括为何走这条集合路径
(如"用户→订单→商品三层关联以聚合金额").
单集合查询返回 null.

【输出】严格 JSON, 不要任何额外文本:
{{"question_pattern": "...", "route_hint_reason": "..." 或 null}}
"""
