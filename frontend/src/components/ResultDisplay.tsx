/* ════════════════════════════════════════════
 *  查询结果展示 — 图表卡片 + SegmentedControl 切换
 * ════════════════════════════════════════════ */

import React, { useState } from "react";
import { Alert } from "antd";
import type { QueryResponse } from "@/types";
import ChartRenderer from "./ChartRenderer";
import styles from "@/styles/query.module.css";

const chartOptions = [
  { label: "表格", value: "table" },
  { label: "折线图", value: "line" },
  { label: "柱状图", value: "bar" },
  { label: "饼图", value: "pie" },
];

interface Props {
  result: QueryResponse;
}

const ResultDisplay: React.FC<Props> = ({ result }) => {
  const [chartType, setChartType] = useState(result.chart_type);

  if (result.error) {
    return <Alert type="error" message="查询错误" description={result.error} showIcon />;
  }

  // table / card 形态 (含多分类维度落表的场景): 仅保留表格视图, 隐藏 line/bar/pie 切换
  const restricted = result.chart_type === "table" || result.chart_type === "card";
  const visibleOptions = restricted
    ? chartOptions.filter((opt) => opt.value === result.chart_type)
    : chartOptions;

  return (
    <div className={styles.chartCard}>
      <div className={styles.chartHeader}>
        <span className={styles.resultMeta}>
          共 {result.row_count} 条结果
        </span>
        <div className={styles.chartSwitcher}>
          {visibleOptions.map((opt) => (
            <button
              key={opt.value}
              className={
                chartType === opt.value
                  ? styles.chartSwitcherItemActive
                  : styles.chartSwitcherItem
              }
              onClick={() => setChartType(opt.value as typeof chartType)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
      {result.performance_warning && (
        <Alert
          type="warning"
          message="性能提示"
          description={result.performance_warning}
          showIcon
          style={{ marginBottom: 12 }}
        />
      )}
      <ChartRenderer result={result} chartType={chartType} />
    </div>
  );
};

export default ResultDisplay;
