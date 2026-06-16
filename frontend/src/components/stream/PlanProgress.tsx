import React from "react";
import { Steps, Tag } from "antd";
import { CheckCircleTwoTone, LoadingOutlined } from "@ant-design/icons";

export interface PlanStepDone {
  step_id: number;
  db_type: string;
  target: string;
  row_count: number;
  exports: string[];
}

interface Props {
  steps: PlanStepDone[];
  /** 当前是否还在执行 plan (有更多 step 待完成) */
  running?: boolean;
}

/**
 * 跨源 plan 多步进度组件 — 渲染 plan_step_done SSE 事件序列.
 * 每个 step 显示 db_type 标签 + target + row_count.
 */
export const PlanProgress: React.FC<Props> = ({ steps, running }) => {
  if (steps.length === 0) return null;

  const items = steps.map((s) => ({
    title: (
      <span>
        <Tag color={s.db_type === "mysql" ? "blue" : s.db_type === "oracle" ? "red" : "green"}>{s.db_type}</Tag>
        {s.target}
      </span>
    ),
    description: `${s.row_count} rows${s.exports.length ? ` → exports: ${s.exports.join(", ")}` : ""}`,
    icon: <CheckCircleTwoTone twoToneColor="#52c41a" />,
  }));

  if (running) {
    items.push({
      title: <span>执行中...</span>,
      description: "",
      icon: <LoadingOutlined />,
    });
  }

  return (
    <div style={{ padding: "12px 0" }}>
      <Steps
        direction="vertical"
        size="small"
        current={steps.length}
        items={items}
      />
    </div>
  );
};
