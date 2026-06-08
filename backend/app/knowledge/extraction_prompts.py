"""Extraction prompt templates + LLM retry wrapper.

All prompts follow prompt-engineering-2026 standard:
- D1: Minimal-but-sufficient
- D2: Role + Goal + Constraints + Output format
- D3: XML delimiters for variable inputs
- D4: Affirmative instructions
- D5: 2-4 canonical examples (generic e-commerce domain)
- D7: Explicit "if uncertain" escape
- Section 3: Zero customer domain words
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
#  load_prompt_or_fallback — prompt_loader 优先, 常量兜底
# ════════════════════════════════════════════════════════════════

_PROMPT_FALLBACK_MAP: dict[str, str] = {}  # populated after constants defined below


def load_prompt_or_fallback(name: str) -> str:
    """Try loading prompt from prompts/ .md file, fall back to in-module constant."""
    try:
        from app.knowledge.prompt_loader import load_prompt
        tpl = load_prompt(name)
        return tpl.body
    except Exception:
        fallback = _PROMPT_FALLBACK_MAP.get(name)
        if fallback:
            return fallback
        raise


# ════════════════════════════════════════════════════════════════
#  ExtractionLLMError — 重试耗尽后抛出, 携带完整重试历史
# ════════════════════════════════════════════════════════════════


@dataclass
class ExtractionLLMError(Exception):
    """LLM extraction failed after all retry attempts."""

    template_name: str
    attempts: list[dict] = field(default_factory=list)
    input_meta: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"ExtractionLLMError(template={self.template_name}, "
            f"attempts={len(self.attempts)})"
        )


# ════════════════════════════════════════════════════════════════
#  with_retry wrapper
# ════════════════════════════════════════════════════════════════


def _is_retryable(exc: BaseException) -> bool:
    """Classify exception: 429/5xx/timeout/empty → retry; 4xx other → no retry."""
    import anthropic
    import openai

    from app.engine.llm import EmptyLLMResponseError

    if isinstance(exc, EmptyLLMResponseError):
        return True

    # OpenAI-compatible (Qwen)
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    if isinstance(exc, openai.RateLimitError):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500  # noqa: hardcode
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return True
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500  # noqa: hardcode

    # Generic timeout
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True

    return False


async def llm_extract_with_retry(
    prompt: str,
    *,
    template_name: str,
    input_meta: dict | None = None,
    max_attempts: int | None = None,
    base_delay: float | None = None,
) -> str:
    """Call chat_completion with exponential backoff retry.

    On final failure, raises ExtractionLLMError with full retry history
    for ExtractionFailureLog writing.

    Backoff schedule: base_delay * (4 ** attempt) → 1s, 4s, 16s, 64s
    """
    from app.engine.llm import chat_completion

    max_attempts = max_attempts or settings.llm_retry_max_attempts
    base_delay = base_delay or settings.llm_retry_base_delay_secs

    attempts: list[dict[str, Any]] = []

    for attempt in range(max_attempts):
        try:
            result = await asyncio.to_thread(
                chat_completion,
                messages=[{"role": "user", "content": prompt}],
            )
            return result
        except Exception as e:
            attempt_record = {
                "attempt": attempt + 1,
                "error_type": type(e).__name__,
                "error_message": str(e)[:500],
                "timestamp": time.time(),
            }

            if _is_retryable(e) and attempt < max_attempts - 1:
                delay = base_delay * (4 ** attempt)
                attempt_record["action"] = "retry"
                attempt_record["delay_secs"] = delay
                attempts.append(attempt_record)
                logger.warning(
                    "llm_extract_with_retry [%s] attempt %d/%d failed (%s), "
                    "retrying in %.1fs",
                    template_name, attempt + 1, max_attempts,
                    type(e).__name__, delay,
                )
                await asyncio.sleep(delay)
            else:
                attempt_record["action"] = "give_up"
                attempts.append(attempt_record)
                raise ExtractionLLMError(
                    template_name=template_name,
                    attempts=attempts,
                    input_meta=input_meta or {},
                ) from e

    # Should not reach here, but safety net
    raise ExtractionLLMError(
        template_name=template_name,
        attempts=attempts,
        input_meta=input_meta or {},
    )


# ════════════════════════════════════════════════════════════════
#  Prompt Templates
# ════════════════════════════════════════════════════════════════


# ── §6.1 Enum Class Extraction ─────────────────────────────────

ENUM_CLASS_EXTRACTION_PROMPT = """\
Role: Java enum 分析器
Goal: 从单个 Java enum 类源码中提取枚举常量名、db_value、description

Constraints:
- db_value 推断: 有构造参数 (如 `CREATED(1, "已创建")`) → 取首位 int 作 db_value
- 无构造参数 (纯名字枚举) → db_value = name (字符串)
- description 推断顺序: Javadoc → 构造参数末位 String → null
- fully_qualified_name 从 package 声明推断
- 如果无法确定某个值, 使用 null

Output format (strict JSON):
{{
  "enum_class": "<类名>",
  "fully_qualified_name": "<包名.类名>",
  "values": [
    {{"name": "<常量名>", "db_value": <int或string>, "description": "<描述或null>"}}
  ]
}}

<examples>
输入:
package com.example.enums;

/**
 * 订单状态枚举
 */
public enum OrderStatus {{
    /** 已创建 */
    CREATED(1, "已创建"),
    /** 已支付 */
    PAID(2, "已支付"),
    /** 已发货 */
    SHIPPED(3, "已发货"),
    /** 已取消 */
    CANCELLED(99, "已取消");

    private final int code;
    private final String desc;

    OrderStatus(int code, String desc) {{
        this.code = code;
        this.desc = desc;
    }}
}}

输出:
{{
  "enum_class": "OrderStatus",
  "fully_qualified_name": "com.example.enums.OrderStatus",
  "values": [
    {{"name": "CREATED", "db_value": 1, "description": "已创建"}},
    {{"name": "PAID", "db_value": 2, "description": "已支付"}},
    {{"name": "SHIPPED", "db_value": 3, "description": "已发货"}},
    {{"name": "CANCELLED", "db_value": 99, "description": "已取消"}}
  ]
}}
</examples>

<enum_source>
{enum_source}
</enum_source>
"""



# ── §6.5 Terminology Extraction ─────────────────────────────────

TERMINOLOGY_EXTRACTION_PROMPT = """\
Role: 业务术语抽取器
Goal: 从 Entity 类和 Mapper SQL 上下文中识别业务名词锚点

Constraints:
- term 为单一名词, 不超过 20 字, 无句号/分号
- term 是业务概念 (如 "订单"), 不是技术名词 (如 "String")
- synonyms 包含英文原名和常见别称
- 如果无法确定某个字段, 使用 null
- 多句业务陈述不属于 terminology, 应走 rule 抽取

Output format (strict JSON):
{{
  "terms": [
    {{
      "term": "<业务名词>",
      "synonyms": ["<同义词1>", "<同义词2>"],
      "primary_collection": "<主集合/表名>",
      "primary_database": null,
      "db_type": "<mysql|mongodb>",
      "primary_field": null,
      "source_collections": ["<相关集合1>", "<相关集合2>"]
    }}
  ]
}}

<examples>
entity_context: "OrderEntity, 字段: orderId(String, 订单号), userId(String, 用户ID), \
totalAmount(BigDecimal, 订单总金额), status(OrderStatus, 订单状态), \
createdAt(Date, 创建时间)"
mapper_context: "SELECT * FROM t_order WHERE user_id = :userId; \
SELECT * FROM t_order WHERE status IN (1,2,3)"

输出:
{{
  "terms": [
    {{
      "term": "订单",
      "synonyms": ["order", "下单"],
      "primary_collection": "t_order",
      "primary_database": null,
      "db_type": "mysql",
      "primary_field": null,
      "source_collections": ["t_order", "t_order_item"]
    }}
  ]
}}
</examples>

<entity_context>
{entity_context}
</entity_context>

<mapper_context>
{mapper_context}
</mapper_context>
"""


# ── §6.6 Business Rule Extraction ──────────────────────────────

BUSINESS_RULE_EXTRACTION_PROMPT = """\
Role: 业务规则分析器
Goal: 从高频 WHERE 条件统计中推断查询业务规则

Constraints:
- rule_kind 三选一: business_constraint / filter_default / join_pattern
- rule_text 可含多句, 描述规则含义
- frequency 来自预统计数据 (Python 计算), LLM 只解释其业务含义
- 如果证据不足以推断规则, 跳过该条件

Output format (strict JSON):
{{
  "rules": [
    {{
      "rule_text": "<规则描述>",
      "applies_to_collections": ["<表/集合名>"],
      "rule_kind": "<business_constraint|filter_default|join_pattern>",
      "evidence": {{
        "frequency": <0.0-1.0>,
        "sample_mappers": ["<Mapper.method>"]
      }}
    }}
  ]
}}

<examples>
where_statistics:
- t_order.is_deleted = 0, frequency: 0.92, mappers: [OrderMapper.selectAll, OrderMapper.selectByUser]
- t_order.status IN (1,2,3), frequency: 0.75, mappers: [OrderMapper.selectActive]
schema_context: "t_order: 订单表, is_deleted: 逻辑删除标记(0=正常,1=已删除), \
status: 订单状态"

输出:
{{
  "rules": [
    {{
      "rule_text": "查询订单时默认排除已删除记录: is_deleted=0",
      "applies_to_collections": ["t_order"],
      "rule_kind": "filter_default",
      "evidence": {{
        "frequency": 0.92,
        "sample_mappers": ["OrderMapper.selectAll", "OrderMapper.selectByUser"]
      }}
    }},
    {{
      "rule_text": "活跃订单筛选: status IN (1,2,3) 排除已取消状态",
      "applies_to_collections": ["t_order"],
      "rule_kind": "business_constraint",
      "evidence": {{
        "frequency": 0.75,
        "sample_mappers": ["OrderMapper.selectActive"]
      }}
    }}
  ]
}}
</examples>

<where_statistics>
{where_statistics}
</where_statistics>

<schema_context>
{schema_context}
</schema_context>
"""


# ── §6.2 Relationship DAO Extraction ───────────────────────────

RELATIONSHIP_DAO_EXTRACTION_PROMPT = """\
Role: Java DAO/Service 关系分析器
Goal: 从 DAO/Service 类源码中识别跨集合/跨表的语义关联模式

Constraints:
- 只识别代码中实际存在的关联模式, 不推测不存在的关联
- 必须返回 evidence.method 定位代码位置
- 识别以下 5 类模式:
  * two_step_query: A.findById → 拿 id → B.findById (两步查询)
  * lookup_pipeline: MongoDB aggregation $lookup 阶段
  * manual_id_filter: A.findAll() 循环中用 a.bId 调 B.find
  * join_method: 方法名暗示 join (如 findOrderWithUser)
  * dbref_dereference: getXxx() 自动反引 DBRef
- 如果代码中无明确关联证据, 返回空 relationships 数组
- snippet 不超过 100 字符, 取最关键的一行代码

Output format (strict JSON):
{{
  "relationships": [
    {{
      "kind": "<two_step_query|lookup_pipeline|manual_id_filter|join_method|dbref_dereference>",
      "from_target": "<源集合/表名>",
      "from_field": "<源字段>",
      "to_target": "<目标集合/表名>",
      "to_field": "<目标字段>",
      "evidence": {{
        "method": "<类名.方法名>",
        "pattern": "<模式类型>",
        "snippet": "<关键代码片段>"
      }}
    }}
  ]
}}

<examples>
输入 dao_source:
public class OrderService {{
    public OrderVO findOrderWithUser(String orderId) {{
        Order order = orderDao.findById(orderId);
        User user = userDao.findById(order.getUserId());
        return new OrderVO(order, user);
    }}
}}

entity_schema_context:
Order: {{orderId: String, userId: String, amount: BigDecimal}}
User: {{id: String, name: String}}

输出:
{{
  "relationships": [{{
    "kind": "two_step_query",
    "from_target": "t_order",
    "from_field": "user_id",
    "to_target": "t_user",
    "to_field": "id",
    "evidence": {{
      "method": "OrderService.findOrderWithUser",
      "pattern": "two_step_query",
      "snippet": "userDao.findById(order.getUserId())"
    }}
  }}]
}}
</examples>

<dao_source>
{dao_source}
</dao_source>

<entity_schema_context>
{entity_schema_context}
</entity_schema_context>
"""


# ════════════════════════════════════════════════════════════════
#  Populate fallback map (constants → prompt names)
# ════════════════════════════════════════════════════════════════

# ── §6.3 MyBatis WHERE 字面量证据 ──────────────────────────

MYBATIS_WHERE_EVIDENCE_PROMPT = """\
Role: MyBatis XML WHERE 条件分析器
Goal: 从 MyBatis mapper XML 中识别 WHERE 子句中的字面量值 (literal) 和占位符 (placeholder)

Constraints:
- 字面量: 直接写死的值, 如 `status = 1`, `type = 'NORMAL'`
- 占位符: MyBatis 参数绑定, 如 `status = #{{status}}`, `id = ${{id}}`
- 只收集字面量到 observed_db_values (int) 或 observed_string_values (string)
- 占位符不计入 observed_* (两个数组都为空)
- table 从 FROM / JOIN 子句推断
- column 从 WHERE 条件左侧推断
- occurrence_count: 该字面量在整个 mapper 中出现的次数
- method_ids: 包含该字面量的 select/update/delete 的 id 列表

Output format (strict JSON):
{{
  "where_evidence": [
    {{
      "table": "<表名>",
      "column": "<字段名>",
      "observed_db_values": [<int值列表>],
      "observed_string_values": ["<string值列表>"],
      "occurrence_count": <int>,
      "method_ids": ["<method_id列表>"]
    }}
  ]
}}

<examples>
输入 XML:
<mapper namespace="OrderMapper">
  <select id="selectByStatus">
    SELECT * FROM t_order WHERE status = 1 OR status = 2
  </select>
  <select id="selectDyn">
    SELECT * FROM t_order WHERE status = #{{status}}
  </select>
</mapper>

输出:
{{
  "where_evidence": [
    {{
      "table": "t_order",
      "column": "status",
      "observed_db_values": [1, 2],
      "observed_string_values": [],
      "occurrence_count": 2,
      "method_ids": ["selectByStatus"]
    }}
  ]
}}
</examples>

<xml_source>
${{xml_source}}
</xml_source>

<mapper_namespace>
${{mapper_namespace}}
</mapper_namespace>
"""


# ── §9 Java Skeleton Extract (多轮分层展开 Round 1) ──────────────

_JAVA_SKELETON_EXTRACT_FALLBACK = """\
Role: Java schema 静态分析助手
Goal: 输出 @Document 标注类的顶层字段骨架, 不递归展开嵌套类型, 同时列出后续轮次需要展开的类型名
Constraints:
  - 仅收录被 @Document 直接标注或在 hidden_entity_collections 中出现的类
  - 顶层字段全部输出: name / type / description (从 javadoc 或 @Field 注释)
  - 字段类型不在叶子白名单 (String, int, long, short, byte, float, double, boolean, char,
    Date, LocalDate, LocalDateTime, Instant, BigDecimal, ObjectId, byte[], UUID,
    Map<String, primitive>, List<primitive>) 时, 标记 needs_expansion: true 且不展开 sub_fields
  - 收集所有 needs_expansion 字段引用的类型名, 去重后写入 types_to_expand
  - 内部静态类引用使用限定名 (例如 User.AddressTag), 跨文件引用使用短名
  - enum_class_hint 仅写 simple name, 无包路径
  - 证据不足时, 字段 description 写 null, 不要编造
Output format: 严格 JSON, 无 markdown fence, 无前后散文

Self check before return:
- mongo_docs 是数组
- 每个 mongo_doc 含 class_name / collection / fields / file
- types_to_expand 与 mongo_docs[*].fields 中 needs_expansion=true 的类型名一一对应
- JSON 解析后无尾随逗号、无注释
"""

# ── §10 Java Type Expand (多轮分层展开 Round 2) ──────────────

_JAVA_TYPE_EXPAND_FALLBACK = """\
Role: Java schema 静态分析助手
Goal: 对指定目标类输出完整字段定义, 递归展开嵌套类型直到叶子节点
Constraints:
  - 只输出 target_classes 列表中的类, 其它类一律不要输出
  - 每个类的 fields 递归展开 sub_fields 直到叶子类型
  - 叶子白名单同 09 prompt: String / int / long / short / byte / float / double / boolean / char /
    Date / LocalDate / LocalDateTime / Instant / BigDecimal / ObjectId / byte[] / UUID /
    Map<String, primitive> / List<primitive>
  - 自引用类型 (例如 TreeNode 含 children: List<TreeNode>): 第二次出现时
    标记 needs_expansion: true, sub_fields 留空
  - 单字段最大递归深度 4 层, 触底时标记 needs_expansion: true, sub_fields 留空
  - 类型限定名匹配优先级: 先按 target_classes 中给的名字精确匹配源码类;
    若 target 是限定名 (Outer.Inner) 找不到时回退查 Inner 短名;
    若 target 是短名 source 中只有限定名时, 用末段匹配
  - 找不到源码的目标类: 返回 fields=[], not_found=true, 不要编造字段
  - 字段证据不足时 description 留 null, 不编造
Output format: 严格 JSON, 无 markdown fence, 无前后散文

Self check before return:
- expanded_classes 是数组
- 每个 class_name 都在 target_classes 中
- target_classes 中每一项都在输出里有对应 entry (即使 not_found=true)
- 没有任何 target_classes 之外的类
"""


_PROMPT_FALLBACK_MAP.update({
    "01-enum-class-extraction": ENUM_CLASS_EXTRACTION_PROMPT,
    "02-relationship-dao-extraction": RELATIONSHIP_DAO_EXTRACTION_PROMPT,
    "03-mybatis-where-evidence": MYBATIS_WHERE_EVIDENCE_PROMPT,
    "05-terminology-extraction": TERMINOLOGY_EXTRACTION_PROMPT,
    "06-business-rule-extraction": BUSINESS_RULE_EXTRACTION_PROMPT,
    "09-java-skeleton-extract": _JAVA_SKELETON_EXTRACT_FALLBACK,
    "10-java-type-expand": _JAVA_TYPE_EXPAND_FALLBACK,
})
