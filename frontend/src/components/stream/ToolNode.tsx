import React, { useState } from "react";
import { Tag, Button, Collapse } from "antd";
import {
  LoadingOutlined,
  CheckCircleTwoTone,
  CloseCircleTwoTone,
  MinusCircleTwoTone,
} from "@ant-design/icons";
import type { ToolNode as ToolNodeT } from "@/hooks/useAgentStream";

/** 尝试将字符串解析为 JSON 并格式化，失败则原样返回 */
function tryFormatJSON(s: string): string {
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  pending: <LoadingOutlined aria-label="pending" />,
  ok: <CheckCircleTwoTone twoToneColor="#52c41a" aria-label="ok" />,
  error: <CloseCircleTwoTone twoToneColor="#ff4d4f" aria-label="error" />,
  cancelled: <MinusCircleTwoTone twoToneColor="#faad14" aria-label="cancelled" />,
};

export const ToolNode: React.FC<{ node: ToolNodeT }> = ({ node }) => {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ padding: "8px 12px", borderLeft: "3px solid #d9d9d9", marginBottom: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {STATUS_ICON[node.status]}
        <strong>{node.name}</strong>
        <Tag>{node.toolCallId.slice(0, 8)}</Tag>
        <Button size="small" type="link" onClick={() => setOpen(!open)}>{open ? "hide detail" : "show detail"}</Button>
      </div>
      {open && (
        <Collapse size="small" defaultActiveKey={["input", "output"]} style={{ marginTop: 8 }}
          items={[
            { key: "input", label: "input", children: <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{JSON.stringify(node.input, null, 2)}</pre> },
            ...(node.output !== undefined ? [{ key: "output", label: "output", children: <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{typeof node.output === "string" ? tryFormatJSON(node.output) : JSON.stringify(node.output, null, 2)}</pre> }] : []),
          ]}
        />
      )}
    </div>
  );
};
