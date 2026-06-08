"""Tool registry + system prompt template — 10 多态工具 (Stage 3 升级).

10 个 agent tool 的统一注册表 (name → callable) 和 LLM 输入 schema 描述.

设计约束:
- input_schema 只暴露 LLM-visible 字段, 不含 runtime context (db / namespace_id /
  ns_slug / trace_id / sse_emit) — 这些由 agent_loop dispatcher 注入.
- TOOL_SPECS 的 name 必须严格对齐 @observe(name="tool.<name>") 字符串.
- 数据访问类工具必传 (db_type, database, target) 三件套.
"""
from __future__ import annotations

from collections.abc import Callable

from app.engine.tools.data_access_tools import (
    estimate_cost,
    execute_query,
    fetch_schema,
    inspect_values,
)
from app.engine.tools.interaction_tools import clarify_with_user
from app.engine.tools.knowledge_tools import lookup_knowledge, save_knowledge
from app.engine.tools.plan_tools import (
    execute_plan_tool,
    generate_query_plan,
    recommend_chart_tool,
)

# ════════════════════════════════════════════
#  数据型 / 图表型 tool 集中常量
#  api/query.py final_answer 反扫 tool_trace 与 _write_query_history 共用
# ════════════════════════════════════════════
EXEC_TOOLS: tuple[str, ...] = (
    "execute_query",
    "execute_plan",
)
"""产生最终结果数据的 tool — final_answer 反扫提取 rows/columns/count 等."""

CHART_TOOLS: tuple[str, ...] = ("recommend_chart",)
"""产生图表推荐的 tool — final_answer 反扫提取 chart_type."""


# ════════════════════════════════════════════
#  REGISTRY — name → callable (LLM tool name 对齐)
# ════════════════════════════════════════════

REGISTRY: dict[str, Callable] = {
    # 知识层
    "lookup_knowledge": lookup_knowledge,
    "save_knowledge": save_knowledge,
    # 数据访问 (多态, 支持 MySQL + MongoDB)
    "fetch_schema": fetch_schema,
    "inspect_values": inspect_values,
    "estimate_cost": estimate_cost,
    "execute_query": execute_query,
    # 用户交互
    "clarify_with_user": clarify_with_user,
    # Plan 生成 / 执行 / 图表
    "generate_query_plan": generate_query_plan,
    "execute_plan": execute_plan_tool,
    "recommend_chart": recommend_chart_tool,
}


# ════════════════════════════════════════════
#  工具 input 字段映射 — 抽取器消费方真相源
# ════════════════════════════════════════════
# 历史教训 (2026-05-14): extractor-protocol stage Task 2 写的 helper 在
# api/query.py 把工具名/字段名硬编码 (fetch_collection_schema / input.collection),
# 但 stage 3 多态化把工具改名为 fetch_schema / inspect_values / execute_query,
# 字段从 collection 改 target. 抽取器看不到 stage 3 工具 → 知识沉淀失效.
# 本常量集中维护工具→字段映射, 工具改名时仅改这里一处.

# 数据访问工具的 collection-target 字段名 (空字符串 = 该工具不持有 target).
TOOL_TARGET_FIELD: dict[str, str] = {
    # 4 件套数据访问工具 (stage 3 多态)
    "fetch_schema":      "target",
    "inspect_values":    "target",
    "estimate_cost":     "target",
    "execute_query":     "target",
    # 多步执行 (走 plan.steps[].collection, 不直接持有 target)
    "execute_plan":        "",
    "generate_query_plan": "",
    # 非数据工具
    "recommend_chart":   "",
    "lookup_knowledge":  "",
    "save_knowledge":    "",
    "clarify_with_user": "",
}

# "真探查"工具: 表明 LLM 主动获取 collection 元信息或字段值,
# field_mappings 应只取真探查证据 (execute_query/estimate_cost 是"用结果", 不算).
PROBE_TOOLS: frozenset[str] = frozenset({"fetch_schema", "inspect_values"})

# 字段值探查工具 (有 field 入参, 用于 _extract_field_mappings 区分 schema vs 字段探查).
FIELD_PROBE_TOOLS: frozenset[str] = frozenset({"inspect_values"})


# ════════════════════════════════════════════
#  TOOL_SPECS — Anthropic tool schema
#  仅暴露 LLM-visible 字段, 不含 runtime context kwargs
# ════════════════════════════════════════════

_LOOKUP_ENTRY_TYPES = ["instance_alias", "example", "rule", "route_hint"]
_SAVE_ENTRY_TYPES = ["terminology", "instance_alias", "example", "rule", "route_hint"]


TOOL_SPECS: list[dict] = [
    # ── 知识层 ──
    {
        "name": "lookup_knowledge",
        "description": (
            "从知识库检索相关条目. "
            "Use when: 问题里出现未在锚点覆盖的业务名词/别名, "
            "或多层关联查询前需要历史路径骨架. "
            "Do not use when: 锚点已覆盖该术语, 或 fetch_schema 已拿到足够信息. "
            "返回: 每条含 content/entry_type/distance/payload."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "中文检索语"},
                "types": {
                    "type": "array",
                    "items": {"enum": _LOOKUP_ENTRY_TYPES},
                    "description": "限定 entry_type 子集",
                },
                "k": {"type": "integer", "description": "Top-K 数量"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "save_knowledge",
        "description": (
            "把本轮会话学到的知识写入知识库待审池. "
            "Use when: clarify 获得用户确认后沉淀可复用知识. "
            "Do not use when: 信息仅对当前查询有效, 或锚点已覆盖. "
            "payload 按 entry_type 不同:\n"
            "- terminology: {term, primary_collection, primary_database, "
            "db_type: mysql|mongodb, synonyms?:[], source_collections?:[]}\n"
            "- instance_alias: {alias, target_collection, target_database, "
            "target_id, id_field?:'_id', canonical_name?}\n"
            "- example: {question, target_collection, "
            "query_json:{pipeline:[...]或filter:{...}}, result_summary?}\n"
            "- rule: {rule_text, applies_to_collections?:[]}\n"
            "- route_hint: {question_pattern, collection_path:[], reason?}\n"
            "返回 {entry_id, status} 或 {success:false, reason}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_type": {"enum": _SAVE_ENTRY_TYPES},
                "content": {"type": "string"},
                "payload": {"type": "object"},
                "evidence": {"type": "object"},
                "tier": {"enum": ["normal", "critical"]},
            },
            "required": ["entry_type", "content", "payload", "evidence"],
        },
    },
    # ── 数据访问 (多态) ──
    {
        "name": "fetch_schema",
        "description": (
            "拉取目标表/集合的完整 schema 真相源. "
            "Use when: 需要确认字段名/嵌套层级/索引/枚举值/外键关系, 或确认目标存在. "
            "Do not use when: 此前 fetch_schema 已拉过同一 target, 上下文已含 schema. "
            "输入 (db_type, database, target) 三件套, db_type 从锚点读. "
            "返回 {target, description, fields, indexes, relationships, source}. "
            "fields[] 含 {name, type, description, nullable, enum_values: [{name, db_value, description}]}; "
            "字段语义看 description, 枚举值看 enum_values, 不要凭字段名猜值. "
            "indexes[] 含已建索引, 决定 WHERE 子句性能. "
            "relationships[] 含 {from_target, from_field, to_target, to_field, relation_type}, "
            "是从代码 (JPA / MyBatis JOIN / DBRef / FK) 抽出的实际使用关联, 命中项可信. "
            "但这是 best-effort 抽取, 不等于完整声明 — 业务实际可能存在更多关联但未在代码中显式表达. "
            "关联推理决策: 判断本次查询所需的关联是否被 relationships[] 覆盖: "
            "覆盖→直接采纳, 不再补查; "
            "部分覆盖 (主路径在但中间跳缺失) 或完全未覆盖→视为信息不足. "
            "信息不足时, 不要凭 fields[] 字段名猜测 (例如看到 user_id 就默认指向 t_user 是错的), "
            "也不要直接断定无关联. 改去 lookup_knowledge 二次召回业务沉淀; "
            "仍无答案再 clarify_with_user."
            " 输出还含 server_capabilities: {version, flavor, unsupported_ops, "
            "unsupported_stage_variants, syntax_constraints, equivalent_hints}. "
            "构造 aggregate pipeline 前必须三项全比对: "
            "(1) 用到的算子是否在 unsupported_ops; "
            "(2) 用到的 stage 形态是否在 unsupported_stage_variants "
            "(例如 $lookup 的 let/pipeline 子查询形态); "
            "(3) 写法是否触犯 syntax_constraints (例如 $project 内 $ 前缀 fieldpath). "
            "命中任一项时, 在 equivalent_hints 里按 restriction 找对应 suggestion, "
            "改用其给出的等效写法; 无对应 hint 时改用不命中该限制的等价表达."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "db_type": {"type": "string", "description": "从锚点 [...] 部分读"},
                "database": {"type": "string"},
                "target": {"type": "string", "description": "表名/集合名"},
            },
            "required": ["db_type", "database", "target"],
        },
    },
    {
        "name": "inspect_values",
        "description": (
            "探目标字段的 distinct 值分布 (默认 top 10). "
            "Use when: 需要判断字段形态(枚举/ID格式/数值区间). "
            "Do not use when: fetch_schema 已列明枚举. "
            "返回 {values: [...]} 的 top-N 频次列表."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "db_type": {"type": "string", "description": "从锚点读"},
                "database": {"type": "string"},
                "target": {"type": "string"},
                "field": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["db_type", "database", "target", "field"],
        },
    },
    {
        "name": "estimate_cost",
        "description": (
            "预估查询扫描行数与风险等级. "
            "Use when: 大表查询前自评. "
            "Do not use when: 目标明显是小表. "
            "返回 {estimated_rows, warning_level: ok|high|blocked}."
            " 输出还含 server_capabilities: {version, flavor, unsupported_ops, "
            "unsupported_stage_variants, syntax_constraints, equivalent_hints}, "
            "与 fetch_schema 同义, 任一处获取即可; "
            "构造 pipeline 前三类限制全比对, 命中按 equivalent_hints 改用等效写法."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "db_type": {"type": "string", "description": "从锚点读"},
                "database": {"type": "string"},
                "target": {"type": "string"},
                "query": {
                    "type": "object",
                    "description": (
                        "查询载荷. MySQL: {sql:'SELECT...'}, "
                        "MongoDB: {pipeline:[...]} 或 {filter:{...}}"
                    ),
                },
            },
            "required": ["db_type", "database", "target", "query"],
        },
    },
    {
        "name": "execute_query",
        "description": (
            "执行查询, 按 mode 控制粒度. "
            "Use when: 已拿到 schema 和过滤条件, 准备实际取数. "
            "Do not use when: 跨 db_type 或跨 database (用 generate_query_plan). "
            "mode: count=只数行, probe=小探查(limit 10), "
            "single=完整结果, batched=分批. "
            "query 形态: MySQL 用 {sql:'...'}, MongoDB 用 {pipeline:[...]}. "
            "返回 {rows, row_count, truncated, elapsed_ms}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "db_type": {"type": "string", "description": "从锚点读"},
                "database": {"type": "string"},
                "target": {"type": "string"},
                "query": {
                    "type": "object",
                    "description": (
                        "MySQL: {sql:'SELECT...'}, "
                        "MongoDB: {pipeline:[...]} 或 {filter:{...}}"
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["single", "probe", "count", "batched"],
                    "default": "single",
                },
                "batch_size": {"type": "integer", "default": 1000},
            },
            "required": ["db_type", "database", "target", "query"],
        },
    },
    # ── 用户交互 ──
    {
        "name": "clarify_with_user",
        "description": (
            "向用户提澄清问题, 阻塞等待回答或超时. "
            "Use when: 多候选无法自决, 或用户需求有歧义. "
            "Do not use when: 信息可通过再调一次工具得到. "
            "返回 {user_answer, timed_out}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": ["question", "options", "reason"],
        },
    },
    # ── Plan 编排 ──
    {
        "name": "generate_query_plan",
        "description": (
            "把已备齐的上下文交给规划器, 生成可串行执行的跨引擎多步查询 Plan. "
            "Use when: 已确认涉及的 库.集合 及其 schema, 且需要多步或跨库/跨引擎(MySQL↔MongoDB)编排. "
            "Do not use when: 单集合单步可直接 execute_query, 或字段/集合仍需探查. "
            "Input example: collections=[{\"db_type\":\"mysql\",\"database\":\"shop_db\",\"collection\":\"orders\"},"
            "{\"db_type\":\"mongodb\",\"database\":\"catalog_db\",\"collection\":\"products\"}], "
            "filters=[{\"collection\":\"orders\",\"field\":\"status\",\"op\":\"eq\",\"value\":1}], "
            "schemas={\"orders\":{\"fields\":[\"id\",\"product_id\",\"status\"]}}. "
            "Output: {plan:{strategy, steps:[{step_idx, db_type, database, collection, operation, ...}]}} — 交 execute_plan 执行."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "用户原始中文问题"},
                "collections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "db_type": {"enum": ["mysql", "mongodb"]},
                            "database": {"type": "string"},
                            "collection": {"type": "string", "description": "集合名(Mongo)或表名(MySQL)"},
                        },
                        "required": ["db_type", "database", "collection"],
                    },
                    "description": "Plan 涉及的全部 库.集合, 每步执行需要 db_type + database",
                },
                "filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "collection": {"type": "string"},
                            "field": {"type": "string"},
                            "op": {"type": "string", "description": "如 eq / regex / gt / in"},
                            "value": {},
                        },
                    },
                    "description": "可选过滤提示, 规划器结合 rules/schema 收敛为最终查询条件",
                },
                "schemas": {
                    "type": "object",
                    "description": "各集合/表 schema, key=集合名或表名, value=字段定义",
                },
                "knowledge": {"type": "array", "items": {"type": "string"}},
                "rules": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["question", "collections", "schemas"],
        },
    },
    {
        "name": "execute_plan",
        "description": (
            "串行执行 multi-step Plan, 返最终行+列名+步骤 trace. "
            "Use when: generate_query_plan 已产出合法 plan. "
            "Do not use when: plan 尚未成形. "
            "返回 {rows, columns, last_step_idx}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": {"type": "object", "description": "QueryPlan dict"},
            },
            "required": ["plan"],
        },
    },
    {
        "name": "recommend_chart",
        "description": (
            "图表推荐 + 展示列指定. "
            "Use when: execute_query 或 execute_plan 返回非空 rows 后, 决定前端渲染类型并指定展示维度. "
            "Do not use when: rows 为空或仅有 count 单值. "
            "返回 {chart_type, config, category_column}. chart_type ∈ {card, line, pie, bar, table}. "
            "Input example: rows=[{\"categoryName\":\"电子产品\",\"count\":120,\"percentage\":45.5},...], "
            "columns=[\"categoryName\",\"count\",\"percentage\"], category_column=\"categoryName\"."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "execute_query 返回的 rows. "
                        "允许补充人类可读列 (如将数字编码翻译为中文名称), "
                        "补充的列应作为 category_column 指定."
                    ),
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "rows[0] 的全部 dict key 列表, 顺序决定表格展示顺序. "
                        "例: [\"categoryName\", \"count\", \"percentage\"]."
                    ),
                },
                "category_column": {
                    "type": "string",
                    "description": (
                        "指定用于图表分类轴的列名 (饼图 name / 折线图 x 轴 / 柱状图 x 轴). "
                        "必须是 columns 中的一个值. "
                        "选择人类可读的文本列, 不要选数字编码列. "
                        "例: rows 含 categoryId=5 和 categoryName='电子产品' 时, "
                        "指定 category_column='categoryName'."
                    ),
                },
            },
            "required": ["rows", "columns", "category_column"],
        },
    },
]


# ════════════════════════════════════════════
#  System Prompt — config 值在构建时注入
# ════════════════════════════════════════════

SYSTEM_PROMPT_TEMPLATE = """你是数据分析助手 (NL2Query). 给定用户的中文问题, \
一步步把它翻成可执行的数据查询并返回结果.

[业务术语锚点] 的 `[db_type]` 前缀决定走哪个驱动; \
所有数据访问类工具都按锚点给出的 (db_type, database, target) 三件套执行.

# 工作流骨架

1. **读上下文**: 先看 [业务术语锚点] — 用户问题里的已知业务实体已预注入, \
格式 `术语 → [db_type] database.target`.
2. **补缺知识**: 锚点未覆盖的业务名词/别名 → lookup_knowledge. \
types 按需选择: instance_alias(别名→记录ID) / example(历史成功对) / route_hint(关联路径) / rule(查询规则). \
不传 types 则全类型混合检索.
3. **确认 schema**: 不熟悉字段名时调 fetch_schema. \
字段值不明调 inspect_values.
4. **验证候选规模**: 模糊指代 → execute_query(mode="probe") 小样本验证. \
0 命中或异常多 → 自决重试/改字段/clarify_with_user.
5. **歧义澄清**: 多候选无法自决 → clarify_with_user. \
用户回答后 save_knowledge 沉淀.
6. **代价评估**: 大表查询前先 estimate_cost. \
只要行数用 execute_query(mode="count"). \
估算超阈值 → execute_query(mode="batched") 分批.
7. **执行**: 单源单步 → execute_query(mode="single") + recommend_chart. \
多步或跨源 → generate_query_plan → execute_plan.
8. **能力兼容**: 构造 aggregate pipeline 前, 比对 fetch_schema / estimate_cost 返回的 \
server_capabilities 三类限制 (unsupported_ops / unsupported_stage_variants / syntax_constraints). \
命中任一项时按 equivalent_hints 改用等效写法; 无 hint 时改用不命中该限制的等价表达.

# 数据源协议

数据访问类工具 (fetch_schema/inspect_values/estimate_cost/execute_query) \
必传 (db_type, database, target) 三件套:
- db_type 从锚点 `[...]` 读, 不要猜
- query 字段形态由 db_type 决定: \
MySQL 用 {{sql: "SELECT ..."}}, MongoDB 用 {{pipeline: [...]}} 或 {{filter: {{...}}}}
- 结果中的 DBRef 字段呈现为 {{$ref: 目标集合名, $id_str: 目标记录ID字符串}}; \
需关联时取 $id_str 当普通字符串匹配目标集合的关联字段.

# 代价控制铁律

- 关联超 2 层: 先 estimate_cost 看每层规模.
- 任一层估算 > {single_layer_limit:,} 行 → execute_query(mode="batched") 分批.
- 三层连乘估算 > {total_limit:,} 行 → clarify_with_user 询问分组策略.
- 用户只问 "个数/占比" → execute_query(mode="count") 短路.

# 死循环规避

同一 tool 同样参数不要连调 — 检测到重复立即停, 改策略或 clarify_with_user.

# 证据不足时

宁可 clarify_with_user 或返回部分结果, 不要编造字段名/表名/集合名/枚举值.

{critical_section}

{anchors_section}

{route_hints_section}

{reflection_section}
"""


def build_system_prompt(
    *,
    settings,
    namespace,
    anchors: list | None = None,
    critical: list | None = None,
    route_hints: list | None = None,
) -> str:
    """注入 config 阈值 + 知识段渲染 system prompt."""
    _ = namespace
    critical_section = ""
    if critical:
        lines = ["## 关键规则 (critical)"]
        lines.extend(f"- {c}" for c in critical)
        critical_section = "\n".join(lines)

    anchors_section = ""
    if anchors:
        lines = ["## 业务术语锚点 (terminology)"]
        for a in anchors:
            syn = f" (同义: {', '.join(a.synonyms)})" if a.synonyms else ""
            lines.append(
                f"- {a.term}{syn} → [{a.db_type}] "
                f"{a.database}.{a.target}"
            )
        anchors_section = "\n".join(lines)

    route_hints_section = ""
    if route_hints:
        lines = ["## 路由提示 (route_hint)"]
        for r in route_hints:
            path = " → ".join(r.collection_path) if r.collection_path else "(空)"
            reason = f" — {r.reason}" if r.reason else ""
            lines.append(f"- 模式: {r.question_pattern} | 路径: {path}{reason}")
        route_hints_section = "\n".join(lines)

    # ── Stage 2 抓手 C: Self-RAG reflection — 走模板变量, 与 critical/anchors/route_hints
    #    同模式, 避免再被拼接到末尾时与 SYSTEM_PROMPT_TEMPLATE 内的工作流编号 (例如 8.) 撞号.
    reflection_section = ""
    if settings.agent_reflection_enabled:
        reflection_section = (
            "## 反思日志 (reflection)\n"
            "\n"
            "每次工具调用前在 text 块内输出反思, 用结构化锚点 `[REFLECTION]...[/REFLECTION]` "
            "包裹 (没有锚点的内容会被跳过):\n"
            "\n"
            "[REFLECTION]\n"
            "confidence: 0.8\n"
            "reason: 锚点未覆盖 VIP, 调 lookup_knowledge\n"
            "alternative: 直接 fetch_schema 但术语不明会浪费查询\n"
            "[/REFLECTION]\n"
            "\n"
            "字段说明:\n"
            "- confidence: 0.0-1.0, 你对此次决策的信心\n"
            "- reason: ≤30 字, 为什么调这个 tool\n"
            "- alternative: 你考虑过但放弃的备选 tool, 留空表示无\n"
            "\n"
            "这段不影响业务, 仅供运维 dashboard 统计 (你不会读到自己的过往 reflection)."
        )

    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        single_layer_limit=settings.query_cost_single_layer_limit,
        total_limit=settings.query_cost_total_limit,
        critical_section=critical_section,
        anchors_section=anchors_section,
        route_hints_section=route_hints_section,
        reflection_section=reflection_section,
    )

    return prompt
