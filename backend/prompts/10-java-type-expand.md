# Prompt: 10-java-type-expand

## Name
java_type_expand

## Input shape
- `java_files`: Round 1 同一 batch 的全部源文件 (seed + ref)
- `target_classes`: list[str] — 本轮需展开的类型名 (限定名 `Outer.Inner` 或短名)

## Expected output shape (JSON)
```json
{
  "expanded_classes": [
    {
      "class_name": "str (与 target_classes 中元素一致)",
      "fields": [
        {
          "field": "str",
          "type": "str",
          "description": "str | null",
          "enum_class_hint": "str | null",
          "needs_expansion": "bool (optional, 自引用 / 深度截断时为 true)",
          "sub_fields": [
            {"field": "...", "type": "...", "description": "..."}
          ]
        }
      ]
    }
  ]
}
```

## Failure handling (validation rules)
- `expanded_classes` 非数组拒收
- 每个 entry 的 `class_name` 必须在 `target_classes` 中
- `target_classes` 中找不到对应源码的类: 输出 `{"class_name": <name>, "fields": [], "not_found": true}`
- 输出非合法 JSON → 调用方 `parse_llm_json` 5 阶段 fallback 仍失败时 `_call_round2_expand` 抛 ValueError

## Failure handling
- LLM HTTP 4xx/5xx/timeout: 调用方 (`_call_round2_expand`) 直接抛出, 由 `_parse_complex_batch_multi_round` 走"5 → 3 类降级重试一次"路径
- 单批超时同上, 降级 fallback 类数 (default 5 → 3) 重试一次, 再失败则该批标记 partial=True 跳过
- 输出非 JSON / 缺 expanded_classes → 调用方抛 ValueError, 由上层降级路径接住
- 注: 多轮 sync 路径不接 `with_retry`, 不写 `ExtractionFailureLog` (见 01-design.md §5)

## 模板正文

```
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

I don't know 指引 (证据不足时的退路, 优先选择空值而非编造):
  - target_class 在源码中找不到 → {"class_name": <name>, "fields": [], "not_found": true}
  - 字段没有 javadoc / @Field 注释 → description 留 null, 不要编造
  - 类型确实自引用 (TreeNode 包含 List<TreeNode>) → 第二次遇到时 needs_expansion=true + sub_fields=[]
  - 递归深度触底 (4 层) → needs_expansion=true + sub_fields=[]

Output format: 严格 JSON, 无 markdown fence, 无前后散文

Self check before return:
- expanded_classes 是数组
- 每个 class_name 都在 target_classes 中
- target_classes 中每一项都在输出里有对应 entry (即使 not_found=true)
- 没有任何 target_classes 之外的类
```

## Few-shot example (canonical, generic e-commerce domain)

Input:
- `target_classes`: `["Address", "OrderRef", "User.AddressTag", "TreeNode"]`
- `java_files`:

```java
// === 文件: User.java ===
package com.example.shop.user;

import org.springframework.data.mongodb.core.mapping.Document;
import java.util.List;

@Document(collection = "users")
public class User {
    private String id;
    private Address addr;
    private List<OrderRef> orderRefs;
    private List<AddressTag> tags;

    public static class AddressTag {
        private String label;
        private boolean primary;
    }
}

// === 文件: Address.java ===
package com.example.shop.user;

public class Address {
    private String street;
    private String city;
    private GeoPoint geo;
}

// === 文件: GeoPoint.java ===
package com.example.shop.user;

public class GeoPoint {
    private double lat;
    private double lng;
}

// === 文件: OrderRef.java ===
package com.example.shop.user;

import java.math.BigDecimal;

public class OrderRef {
    private String orderId;
    private BigDecimal amount;
}

// === 文件: TreeNode.java [参考上下文] ===
package com.example.shop.tree;

import java.util.List;

public class TreeNode {
    private String value;
    private List<TreeNode> children;
}
```

Expected output:
```json
{
  "expanded_classes": [
    {
      "class_name": "Address",
      "fields": [
        {"field": "street", "type": "String", "description": null},
        {"field": "city",   "type": "String", "description": null},
        {"field": "geo",    "type": "GeoPoint", "description": null,
         "needs_expansion": true,
         "sub_fields": [
           {"field": "lat", "type": "double", "description": null},
           {"field": "lng", "type": "double", "description": null}
         ]}
      ]
    },
    {
      "class_name": "OrderRef",
      "fields": [
        {"field": "orderId", "type": "String",  "description": null},
        {"field": "amount",  "type": "BigDecimal", "description": null}
      ]
    },
    {
      "class_name": "User.AddressTag",
      "fields": [
        {"field": "label",   "type": "String",  "description": null},
        {"field": "primary", "type": "boolean", "description": null}
      ]
    },
    {
      "class_name": "TreeNode",
      "fields": [
        {"field": "value",    "type": "String", "description": null},
        {"field": "children", "type": "List<TreeNode>", "description": null,
         "needs_expansion": true, "sub_fields": []}
      ]
    }
  ]
}
```

## I don't know (human-readable summary)

> 实际的 LLM 退路指引已写入上方 `## 模板正文` fence 内 (prompt_loader 只读 fence 内内容). 此处仅供文档阅读者快速回顾:
> - target_class 在源码中找不到 → `{"class_name": <name>, "fields": [], "not_found": true}`
> - 字段没有 javadoc / @Field 注释 → description 留 null
> - 类型确实自引用 → 第二次遇到时 needs_expansion=true + sub_fields=[]
