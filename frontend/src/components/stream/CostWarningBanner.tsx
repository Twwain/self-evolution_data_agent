import React from "react";
import { Alert } from "antd";

// ============================================================================
// CostWarningBanner — 大数据查询预警条
// 千分位格式化, 附 advice 引导用户改进 query
// ============================================================================
interface CostWarning {
  estimatedDocs: number;
  threshold: number;
  advice?: string;
}

export const CostWarningBanner: React.FC<{ warnings: CostWarning[] }> = ({ warnings }) => {
  if (warnings.length === 0) return null;
  return (
    <>
      {warnings.map((w, i) => (
        <Alert
          key={i}
          type="warning"
          showIcon
          style={{ marginBottom: 8 }}
          message={`⚠️ 大数据预警: 预估 ${w.estimatedDocs.toLocaleString()} 行 (阈值 ${w.threshold.toLocaleString()})`}
          description={w.advice}
        />
      ))}
    </>
  );
};
