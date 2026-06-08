# Prompt: 09-java-skeleton-extract

## Name
java_skeleton_extract

## Input shape
- `java_files`: 一批 Java 源文件 (含 seed entity + [参考上下文] DTO/枚举/嵌入类型)
- `hidden_entity_collections`: dict[class_name, collection_name] (DAO 反查得到的隐藏 entity)

## Expected output shape (JSON)
```json
{
  "mongo_docs": [
    {
      "class_name": "str",
      "collection": "str",
      "description": "str | null",
      "purpose_detail": "str | null",
      "fields": [
        {
          "field": "str",
          "type": "str",
          "description": "str | null",
          "enum_class_hint": "str | null",
          "needs_expansion": "bool (optional, true if 非叶子类型)"
        }
      ],
      "file": "str"
    }
  ],
  "types_to_expand": ["str"]
}
```

## Validation rules
- `mongo_docs` 非数组拒收
- `types_to_expand` 元素为去重短名或限定名
- 顶层字段不递归 `sub_fields`
- 输出非合法 JSON → 调用方 `parse_llm_json` 5 阶段 fallback 仍失败时 `_call_round1_skeleton` 返回 None, 整批 errored

## Failure handling
- LLM HTTP 4xx/5xx/timeout: `_call_round1_skeleton` 在 sync `try/except` 中捕获 + return None, 整 batch errored (与单轮 `_call_and_validate_java` 同形)
- 输出非 JSON / 缺 mongo_docs: 同上, return None
- 注: 多轮 sync 路径不接 `with_retry`, 不写 `ExtractionFailureLog` (见 01-design.md §5)

## 模板正文

```
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

I don't know 指引 (证据不足时的退路, 优先选择空值而非编造):
  - 字段没有 javadoc / @Field 注释 → description 留 null, 不要编造
  - 全文件无 @Document 也无 hidden_entity_collections 命中 → 返回 {"mongo_docs": [], "types_to_expand": []}
  - 类型解析模糊 (例如 Object / 泛型擦除) → 不标 needs_expansion, 不进 types_to_expand

Output format: 严格 JSON, 无 markdown fence, 无前后散文

Self check before return:
- mongo_docs 是数组
- 每个 mongo_doc 含 class_name / collection / fields / file
- types_to_expand 与 mongo_docs[*].fields 中 needs_expansion=true 的类型名一一对应
- JSON 解析后无尾随逗号、无注释
```

## Few-shot example (canonical, generic e-commerce domain)

Input (节选):
```java
// User.java
@Document(collection = "users")
public class User {
    @Id private String id;
    private String name;
    private Address address;
    private List<OrderRef> orderRefs;

    public static class AddressTag {
        private String label;
        private boolean primary;
    }
    private AddressTag tag;
}

// Address.java
public class Address {
    private String street;
    private String city;
    private GeoPoint geo;
}
```

Expected output:
```json
{
  "mongo_docs": [
    {
      "class_name": "User",
      "collection": "users",
      "description": null,
      "purpose_detail": null,
      "file": "User.java",
      "fields": [
        {"field": "id",        "type": "String",          "description": null},
        {"field": "name",      "type": "String",          "description": null},
        {"field": "address",   "type": "Address",         "description": null, "needs_expansion": true},
        {"field": "orderRefs", "type": "List<OrderRef>",  "description": null, "needs_expansion": true},
        {"field": "tag",       "type": "User.AddressTag", "description": null, "needs_expansion": true}
      ]
    }
  ],
  "types_to_expand": ["Address", "OrderRef", "User.AddressTag"]
}
```

## I don't know (human-readable summary)

> 实际的 LLM 退路指引已写入上方 `## 模板正文` fence 内 (prompt_loader 只读 fence 内内容). 此处仅供文档阅读者快速回顾:
> - 证据不足 (没有源码或注释) → description 留 null, 不要编造字段.
> - 全文件无 @Document 也无 hidden_entity_collections 命中 → 返回 `{"mongo_docs": [], "types_to_expand": []}`.
