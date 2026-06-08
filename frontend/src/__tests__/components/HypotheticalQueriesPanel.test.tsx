/* ════════════════════════════════════════════════════════════════════════════
 *  HypotheticalQueriesPanel 单测 — 编辑全部按钮 + PUT body 契约
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { HypotheticalQueriesPanel } from "@/components/audit/HypotheticalQueriesPanel";

const mockEditKnowledge = vi.fn().mockResolvedValue({
  entry: { id: 42 },
  conflicts: [],
});

vi.mock("@/api", () => ({
  editKnowledge: (...args: unknown[]) => mockEditKnowledge(...args),
}));

describe("HypotheticalQueriesPanel", () => {
  beforeEach(() => {
    mockEditKnowledge.mockClear();
  });

  it("renders HQ tags from hypothetical_queries_json", () => {
    render(
      <HypotheticalQueriesPanel
        entryId={42}
        hypothetical_queries_json={JSON.stringify([
          { q: "问题1", generated_at: "2026-01-01", model: "qwen-plus" },
          { q: "问题2", generated_at: "2026-01-01", model: "qwen-plus" },
        ])}
      />,
    );
    expect(screen.getByText("问题1")).toBeInTheDocument();
    expect(screen.getByText("问题2")).toBeInTheDocument();
  });

  it("renders empty state when no HQ", () => {
    render(
      <HypotheticalQueriesPanel
        entryId={42}
        hypothetical_queries_json="[]"
      />,
    );
    expect(screen.getByText(/未生成假设触发问题/)).toBeInTheDocument();
  });

  it("编辑全部按钮可见", () => {
    render(
      <HypotheticalQueriesPanel
        entryId={42}
        hypothetical_queries_json={JSON.stringify([
          { q: "旧问题", generated_at: "2026-01-01", model: "qwen-plus" },
        ])}
      />,
    );
    expect(screen.getByRole("button", { name: "编辑全部" })).toBeInTheDocument();
  });

  it("PUT body 含 hypothetical_queries + reason", async () => {
    const onUpdated = vi.fn();
    const { container } = render(
      <HypotheticalQueriesPanel
        entryId={42}
        hypothetical_queries_json={JSON.stringify([
          { q: "旧问题1", generated_at: "2026-01-01", model: "qwen-plus" },
        ])}
        onUpdated={onUpdated}
      />,
    );

    // 点击编辑全部
    await userEvent.click(screen.getByRole("button", { name: "编辑全部" }));

    // antd Modal 渲染到 document.body portal — 用 document.querySelector 找
    await waitFor(() => {
      expect(document.querySelector(".ant-modal")).toBeInTheDocument();
    });

    // textarea 应含旧内容
    const textarea = document.querySelector(".ant-modal textarea") as HTMLTextAreaElement;
    expect(textarea).toBeTruthy();
    expect(textarea.value).toBe("旧问题1");

    // 清空并输入新内容
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "新问题1\n新问题2");

    // 找 Modal footer 的 OK 按钮 (antd 渲染为 .ant-btn-primary 在 .ant-modal-footer 内)
    const modalFooter = document.querySelector(".ant-modal-footer");
    const okBtn = modalFooter?.querySelector(".ant-btn-primary") as HTMLButtonElement;
    expect(okBtn).toBeTruthy();
    await userEvent.click(okBtn);

    await waitFor(() => {
      expect(mockEditKnowledge).toHaveBeenCalledWith(42, {
        hypothetical_queries: ["新问题1", "新问题2"],
        reason: "manual edit HQ",
      });
    });
  });
});
