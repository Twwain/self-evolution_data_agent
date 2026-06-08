# Prompt: semantic-equivalence-candidate

## Name
semantic_equivalence_candidate

## Purpose
判定两个 SchemaCanonicalCandidate 在**字段语义层面**是否等价 — 用于 promote 路径在确定性 helper 全 miss 后的 LLM 兜底. 不是 NL 问题等价, 不是 SQL 等价.

## Input shape
- `kind`: candidate 的 kind (`field_description` / `relationship` / `sample_values`), 决定参考维度
- `db_type`: `mysql` / `mongodb` / 其他, 给模型一点上下文判定生态相关同义
- `target`: 字段所在表/集合名 (generic, 不带客户业务词), 仅作锚点
- `field_path`: 字段路径
- `candidate_a_value`: 第一个 candidate 的 value_json 字面 (description 文本 / sample list / 关系结构)
- `candidate_b_value`: 第二个 candidate 的 value_json 字面

## Expected output shape (strict JSON)
```json
{
  "equivalent": true,
  "confidence": 0.0,
  "reason": "str"
}
```
- `equivalent`: bool, 必填. 严格判等价 — 仅措辞/语序不同视等价, 实际信息含量不同视为不等价.
- `confidence`: 0.0-1.0. < 0.7 视为模型不确定, 调用方应当作 None 处理 (返 conflict).
- `reason`: 一句话, ≤ 50 字, 只描述等价/不等价的依据, 不复述输入.

## Validation rules
- `equivalent` 必须是 boolean (不允许 "true" 字符串)
- `confidence` 必须在 [0.0, 1.0] 闭区间
- `reason` 非空, ≤ 50 字
- 输出无任何 markdown fence / 解释文字 / 思考过程 — **只输出 JSON 对象**

## Failure handling
- LLM HTTP 4xx/5xx → `with_retry` 4 次指数退避 (复用 `app/knowledge/llm_retry.py`)
- 输出非 JSON → checker 返 None (让 registry 链继续到下一条 rule), 同时 ExtractionFailureLog(failure_type=llm_parse_error)
- 输出 JSON 但 `confidence < 0.7` → checker 返 None (不当作等价)
- token 超 `IS_EQUIVALENCE_LLM_BUDGET_PER_BATCH` 配额 → checker 直接返 None, 不发请求

## 模板正文
```text
Role: SchemaCanonical 字段语义等价判断器
Goal: 判定两个候选值是否在字段语义层面等价

Constraints:
- 等价 = 两个候选值表达同一字段的同一信息, 仅表述不同 (同义词 / 语序 / 大小写 / 标点)
- 不等价 = 任一方含有对方没有的实质信息 (额外约束 / 额外枚举值 / 额外子结构)
- 不要尝试推断字段背后的业务含义, 仅从字面语义判断
- confidence 反映判断确信度, 模糊场景给 < 0.7

Output format (strict JSON, no markdown, no prose):
{"equivalent": <true|false>, "confidence": <0.0-1.0>, "reason": "<= 50 chars>"}

Examples:

Example 1 — equivalent (description 同义):
<input>
kind: field_description
db_type: mysql
target: orders
field_path: status
candidate_a_value: "订单当前状态"
candidate_b_value: "订单状态"
</input>
Output: {"equivalent": true, "confidence": 0.95, "reason": "措辞差异, 语义同"}

Example 2 — not equivalent (信息含量差):
<input>
kind: field_description
db_type: mysql
target: products
field_path: price
candidate_a_value: "商品零售价, 单位元"
candidate_b_value: "商品价格"
</input>
Output: {"equivalent": false, "confidence": 0.85, "reason": "A 含单位与零售/批发区分, B 缺"}

Example 3 — equivalent (sample_values 集合等价):
<input>
kind: sample_values
db_type: mongodb
target: users
field_path: gender
candidate_a_value: ["male", "female"]
candidate_b_value: ["female", "male"]
</input>
Output: {"equivalent": true, "confidence": 0.99, "reason": "集合相等, 顺序无关"}

<input>
kind: $kind
db_type: $db_type
target: $target
field_path: $field_path
candidate_a_value: $candidate_a_value
candidate_b_value: $candidate_b_value
</input>
```

## Productization checklist (CLAUDE.md 6 条, 自审通过)
- [x] 无客户专属 collection / database 名 (用 `orders` / `products` / `users` 通用电商)
- [x] 无客户专属业务词 (无领域实体名 / GMV)
- [x] 无客户专属字面 ID / 时间 / 数值阈值
- [x] 示例用通用电商域 (user / order / product / status / price / gender)
- [x] 概念定义采"触发特征 + 通用示例" (等价/不等价各举 generic 反例)
- [x] 模板占位符仅运行时数据 (`$kind` / `$db_type` / `$target` / `$field_path` / `$candidate_a_value` / `$candidate_b_value`)
