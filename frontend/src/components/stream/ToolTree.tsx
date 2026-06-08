import React from "react";
import { Empty } from "antd";
import { ToolNode } from "./ToolNode";
import type { ToolNode as ToolNodeT } from "@/hooks/useAgentStream";

interface Props {
  tools: ToolNodeT[];
  /** 当前 agent 状态, 用于决定空态呈现 */
  status?: "idle" | "running" | "finished" | "cancelled" | "error";
}

export const ToolTree: React.FC<Props> = ({ tools, status }) => {
  if (tools.length === 0) {
    // idle: 用户还没发问, 不展示 "no tool calls yet" 噪音 (底部已有提示)
    if (status === "idle" || status === undefined) return null;
    // running 但还没 tool_use: 给个更友好的文案
    if (status === "running") return <Empty description="agent 正在思考…" />;
    // finished / cancelled / error: 没工具调用就不展示这张卡片
    return null;
  }
  return (
    <div role="list" aria-label="tool tree">
      {tools.map((t) => <ToolNode key={t.toolCallId} node={t} />)}
    </div>
  );
};
