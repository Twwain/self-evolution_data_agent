import React from "react";
import { Collapse } from "antd";

// ============================================================================
// ThinkingBlock — agent 思考过程折叠面板
// 空文本时直接消失, 不占视觉权重
// ============================================================================
export const ThinkingBlock: React.FC<{ text: string }> = ({ text }) => {
  if (!text) return null;
  return (
    <Collapse
      size="small"
      ghost
      defaultActiveKey={["t"]}
      items={[
        {
          key: "t",
          label: "🧠 thinking",
          children: (
            <div style={{ whiteSpace: "pre-wrap", color: "#666" }}>{text}</div>
          ),
        },
      ]}
    />
  );
};
