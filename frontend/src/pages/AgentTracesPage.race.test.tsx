import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// 受控 deferred — 捕获每次 listAgentTraces 的 resolve, 手动控制返回顺序
const deferreds: Array<(v: unknown) => void> = [];
vi.mock("@/api", () => ({
  listAgentTraces: vi.fn(() => new Promise((resolve) => { deferreds.push(resolve); })),
  getAgentTrace: vi.fn(),
  refineAgentTraces: vi.fn(),
}));
// NamespaceSelector 挂载即自动选中 ns=1 (复刻真实 list[0] 自动选中行为 → 触发第二个请求)
vi.mock("@/components/NamespaceSelector", () => ({
  default: ({ onChange }: { onChange: (id: number, ns: unknown) => void }) => {
    setTimeout(() => onChange(1, { id: 1 }), 0);
    return null;
  },
}));

import AgentTracesPage from "./AgentTracesPage";

const _row = (trace_id: string, ns: number, id: number) => ({
  id, trace_id, namespace_id: ns, user_query: "q", status: "completed",
  created_at: "2026-01-01", tool_call_count: 0,
});

describe("AgentTracesPage 竞态守护 (确定性乱序)", () => {
  beforeEach(() => { deferreds.length = 0; });

  it("无过滤响应晚到也不覆盖已过滤结果", async () => {
    render(<AgentTracesPage />);
    // mount → load(ns=undefined) = deferreds[0]; NamespaceSelector 选中 → load(ns=1) = deferreds[1]
    await waitFor(() => expect(deferreds.length).toBe(2));
    // 乱序: 先 resolve 已过滤 (seq=2), 再 resolve 无过滤 (seq=1, 应被守护丢弃)
    deferreds[1]([_row("filtered", 1, 2)]);
    deferreds[0]([_row("unfiltered-stale", 9, 1)]);
    await waitFor(() => expect(screen.getByText("filtered")).toBeInTheDocument());
    expect(screen.queryByText("unfiltered-stale")).not.toBeInTheDocument();
  });
});
