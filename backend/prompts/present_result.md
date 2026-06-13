# present_result — 工具描述 + chart_spec 字段契约

> 用途: agent loop 最终结果渲染工具 description (registry.py TOOL_SPECS) + chart_spec 字段说明
> 入参无 rows 数组: LLM 只给指针 (ref) + 列角色 (chart_spec), 数据不经 LLM 转录.
> 审核通过版 (prompt-engineering-2026 纪律: 通用领域词, 无客户领域词).

## 模板正文

```
present_result: 指定最终结果集与图表渲染方式 (确定性渲染, 数据不经你转录).

<when_to_use>
execute_query 或 execute_plan 成功返回非空结果, 你已确定哪一次执行
是要展示给用户的最终结果集时, 调用本工具收尾.
</when_to_use>

<when_not_to_use>
如果所有 execute_query / execute_plan 都失败了 ——
没有任何一次成功的执行可供 ref 指向 —— 不要调用 present_result. 此时直接用
文字向用户说明查询未能完成及原因, 然后结束本回合. present_result 的 ref
必须指向一次真实成功的执行; 在没有成功结果时调用它, 只会渲染出一张空表格,
反而掩盖了真正的失败.
</when_not_to_use>

<core_rule>
你【不需要】把数据行复制进入参. 只需用 ref 指向那次执行, 渲染器会从服务端取该次
执行的【完整】结果, 按你给的列角色拼图表. 把全部数据行抄进入参是错误用法 —— 你看到的
结果可能已被采样, 抄进来会丢数据.

ref 怎么取: execute_query / execute_plan 成功返回的结果里有一个 result_ref 字段,
直接把它的值原样填进 ref. 不要自己编造 id, 不要凭印象拼 —— 必须从那次执行的返回
结果中复制 result_ref 的真实值.
</core_rule>

<params>
- ref: 目标执行返回结果里的 result_ref 字段值 (字符串). 即 execute_query/execute_plan
    成功后, 其输出 JSON 中 "result_ref" 的值, 原样复制. 它唯一标识那次执行.
- chart_spec: 列角色映射, 字段如下 ——
    - chart_type: 图表类型, 取值 card / line / pie / bar / table.
        card=单值或几个指标卡; line=趋势(随时间/有序维度); pie=占比(分类≤少数);
        bar=分类对比(分类较多); table=多维或无法用上述表达时.
    - x: 充当横轴/分类轴的列名 (line 的横轴 / bar 的横轴 / pie 的扇区名). 必须是结果里的列.
    - series_by: 可选. 若要按某列分出多条系列 (例如按"地区"列分出多条折线对比),
        填该列名; 不需要多系列则留空. 留空=单系列.
    - value: 数值列名 (纵轴值 / 扇区数值). 必须是结果里的列.
    - code_label_map: 可选. 当结果列里是编码而非可读名称且查询未翻译时, 给出
        {列名: {编码: 可读名}} 让渲染器替换全量数据. 例:
        {"category_code": {"1": "电子", "2": "服饰"}}.
</params>

<examples>
1) 多系列折线 (按地区分多条线对比每日销量):
   ref="<上一步 execute_query 返回结果里 result_ref 字段的值>"
   chart_spec={"chart_type":"line","x":"order_day","series_by":"region","value":"total_amount"}

2) 柱状对比 (各分类订单数):
   chart_spec={"chart_type":"bar","x":"category","value":"order_count"}

3) 饼图占比 (各支付方式金额占比):
   chart_spec={"chart_type":"pie","x":"pay_method","value":"amount"}

4) 指标卡 + 编码兜底翻译 (单值; 或编码列翻可读名):
   chart_spec={"chart_type":"card","value":"total_amount"}
   # 若结果列是编码: chart_spec={"chart_type":"bar","x":"category_code","value":"n",
   #   "code_label_map":{"category_code":{"1":"electronics","2":"apparel"}}}
</examples>

<returns>
严格 JSON, 两路径:

成功:
  {"status":"ok","ref":"<tool_call_id>","chart_spec":{"chart_type":"line","x":"order_day","series_by":"region","value":"total_amount"}}
  — chart_spec 已记录, 渲染在 finalization 阶段由服务端按 ref 取全量数据完成.

失败 (ref 无效/未指向成功执行):
  {"status":"error","error_type":"BadRef","error_message":"ref 指向的执行不存在或未成功","suggested_next_step":"检查 ref 是否取自 execute_query/execute_plan 返回结果里的 result_ref 字段值"}
  — 此时按上面逃生口处理: 用文字向用户说明查询未能完成, 不要重试空 present_result.
</returns>
```
