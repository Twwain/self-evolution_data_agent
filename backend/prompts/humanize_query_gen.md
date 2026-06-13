# humanize — 查询生成阶段 system_prompt 段

> 用途: 让 LLM 在生成 SQL/pipeline 时就把编码列翻成可读名 (源头 humanize, 零额外 token)
> 翻不动时走 present_result 的 code_label_map 兜底 (不在本段表达)
> 审核通过版 (prompt-engineering-2026 纪律: 通用领域词, 无客户领域词).

## 模板正文

```
<humanize_display_columns>
展示列输出可读名, 不要输出原始编码:

当某列存的是编码 (如状态码、分类 id、外键 id), 而 schema 里能找到它对应的
可读名称来源时, 在生成查询时就把它翻成可读名 ——

- 有维表/字典表: JOIN 维表, SELECT 取 label 列, 不要 SELECT 编码列.
    例: SELECT d.region_name, SUM(o.amount) ...
        FROM orders o JOIN dim_region d ON o.region_id = d.id
        GROUP BY d.region_name
- 无维表但 schema 标注了枚举取值: 用 CASE WHEN 翻译.
    例: SELECT CASE status WHEN 1 THEN '待处理' WHEN 2 THEN '已完成' END AS status_label,
        COUNT(*) ... GROUP BY status

目标: execute_query 返回的结果直接可读, 用户和后续渲染都不必再处理编码.
若 schema 里找不到编码的可读来源, 保留原列即可 (后续环节会兜底翻译).
</humanize_display_columns>
```
