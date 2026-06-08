# Prompt: 02-relationship-dao-extraction

## Name
relationship_dao_extraction

## Input shape
- dao_source: DAO/Service 类源码
- entity_schema_context: Entity schema 摘要

## Expected output shape (JSON)
```json
{
  "relationships": [
    {
      "kind": "two_step_query|lookup_pipeline|manual_id_filter|join_method|dbref_dereference",
      "from_target": "str",
      "from_field": "str",
      "to_target": "str",
      "to_field": "str",
      "evidence": {"method": "str", "pattern": "str", "snippet": "str"}
    }
  ]
}
```

## Validation rules
- relationships 为数组 (可空)
- kind 必须为 5 类之一
- evidence.method 非空

## Failure handling
- LLM HTTP 4xx/5xx → with_retry 4 次指数退避
- 输出非 JSON → ExtractionFailureLog(failure_type=llm_parse_error)

## 模板正文
```text
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
{
  "relationships": [
    {
      "kind": "<two_step_query|lookup_pipeline|manual_id_filter|join_method|dbref_dereference>",
      "from_target": "<源集合/表名>",
      "from_field": "<源字段>",
      "to_target": "<目标集合/表名>",
      "to_field": "<目标字段>",
      "evidence": {
        "method": "<类名.方法名>",
        "pattern": "<模式类型>",
        "snippet": "<关键代码片段>"
      }
    }
  ]
}

<dao_source>
$dao_source
</dao_source>

<entity_schema_context>
$entity_schema_context
</entity_schema_context>
```
