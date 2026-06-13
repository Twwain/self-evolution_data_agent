# forced_plan_guidance — execute_query 超阈引导语

> 用途: execute_query(single) 结果撞行上限被截断时, 作为 status=error 的 error_message 回喂 LLM
> $row_limit / $estimated 由代码 render 时注入 (string.Template)
> 审核通过版 (prompt-engineering-2026 纪律: 通用领域词, 无客户领域词).

## 模板正文

```
查询已返回约 $estimated 行, 超过单次上限 $row_limit 行, 结果已被截断.
弃用当前截断结果, 不要尝试用更窄的时间窗或更小范围分多次 execute_query 拼接
—— 那样得到的是被切碎的局部结果, 渲染时无法还原成完整答案.

正确做法 (二选一):
- 若需完整结果作为图表数据: 改用 generate_query_plan 生成查询计划, 再用 execute_plan 执行.
  execute_plan 会把结果汇总成单一最终结果集, 作为后续 present_result 的渲染来源.
- 若图表点数过多 (如逐日两年): 改用更粗的聚合粒度 (如按日改按月/按周) 重新生成查询,
  让结果落在可渲染范围内.

若你其实只需要行数而非明细, 改用 execute_query(mode="count");
若只需小样本验证, 用 execute_query(mode="probe").

若 plan 执行后结果仍超限、或更粗粒度已不可行 (无法再聚合): 不要反复重试同类查询.
直接用文字向用户说明结果规模过大无法完整渲染, 建议其缩小时间范围或换更粗的统计粒度, 然后结束本回合.
```
