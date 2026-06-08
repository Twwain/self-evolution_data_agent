/* ════════════════════════════════════════════════════════════════════════════
 *  AuditQueue — 列表/分页/筛选/全选/批量条
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import AuditQueue from "@/components/audit/AuditQueue";

vi.mock("@/api", () => ({
  fetchAuditQueue: vi.fn(),
  approveEntry: vi.fn().mockResolvedValue({}),
  rejectEntry: vi.fn().mockResolvedValue({}),
  fetchAuditLog: vi.fn().mockResolvedValue([]),
  deleteKnowledgeWithMode: vi.fn().mockResolvedValue({}),
  restoreEntry: vi.fn().mockResolvedValue({}),
  editKnowledge: vi.fn().mockResolvedValue({}),
  previewConflict: vi.fn().mockResolvedValue({ conflicts: [] }),
  batchAudit: vi.fn().mockResolvedValue({ affected_count: 1, success_ids: [1] }),
}));

const item = (id: number, content: string) => ({
  id,
  namespace_id: 1,
  entry_type: "terminology" as const,
  tier: "normal" as const,
  content,
  raw_input: content,
  description: "",
  source: "manual" as const,
  status: "proposed" as const,
  is_superseded: false,
  refined_at: null,
  created_at: "2026-05-01T00:00:00Z",
});

beforeEach(() => vi.clearAllMocks());

describe("AuditQueue", () => {
  it("loading → 渲染列表 + total Tag", async () => {
    const { fetchAuditQueue } = await import("@/api");
    (fetchAuditQueue as any).mockResolvedValue({
      items: [item(1, "x"), item(2, "y")],
      total: 2,
      page: 1,
      size: 20,
    });
    render(<AuditQueue nsId={1} />);
    await waitFor(() => expect(screen.getByText("x")).toBeInTheDocument());
    expect(screen.getByText("y")).toBeInTheDocument();
    expect(screen.getByText(/共 2 条/)).toBeInTheDocument();
  });

  it("空列表 → 渲染 Empty", async () => {
    const { fetchAuditQueue } = await import("@/api");
    (fetchAuditQueue as any).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      size: 20,
    });
    const { container } = render(<AuditQueue nsId={1} />);
    await waitFor(() =>
      expect(container.querySelector(".ant-empty")).toBeTruthy(),
    );
  });

  it("勾选条目后 BatchAuditBar 出现", async () => {
    const { fetchAuditQueue } = await import("@/api");
    (fetchAuditQueue as any).mockResolvedValue({
      items: [item(1, "a"), item(2, "b")],
      total: 2,
      page: 1,
      size: 20,
    });
    const user = userEvent.setup();
    render(<AuditQueue nsId={1} status="proposed" />);
    await waitFor(() => expect(screen.getByText("a")).toBeInTheDocument());
    const cbs = document.querySelectorAll(".ant-checkbox-input");
    await user.click(cbs[0] as HTMLElement);
    await waitFor(() => expect(screen.getByText(/已选 1 条/)).toBeInTheDocument());
  });

  it("undefined nsId (全局视角) 也能拉数据", async () => {
    const { fetchAuditQueue } = await import("@/api");
    (fetchAuditQueue as any).mockResolvedValue({
      items: [item(1, "global-only")],
      total: 1,
      page: 1,
      size: 20,
    });
    render(<AuditQueue nsId={undefined} status="canonical" />);
    await waitFor(() => expect(screen.getByText("global-only")).toBeInTheDocument());
    expect(fetchAuditQueue).toHaveBeenCalledWith(
      expect.objectContaining({ namespace_id: undefined, status: "canonical" }),
    );
  });

  it("输入搜索词 → 300ms debounce 后调 fetchAuditQueue 带 q + 重置 page", async () => {
    const { fetchAuditQueue } = await import("@/api");
    (fetchAuditQueue as any).mockResolvedValue({
      items: [], total: 0, page: 1, size: 20,
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<AuditQueue nsId={1} />);

    // 初始拉取一次 (无 q)
    await waitFor(() => expect(fetchAuditQueue).toHaveBeenCalledTimes(1));
    expect((fetchAuditQueue as any).mock.calls[0][0]).not.toHaveProperty("q");

    const input = screen.getByPlaceholderText(/搜索/);
    await user.type(input, "GMV");

    // debounce 内不触发新调用
    expect(fetchAuditQueue).toHaveBeenCalledTimes(1);

    // 推进 350ms 跨过 300ms debounce
    await vi.advanceTimersByTimeAsync(350);

    await waitFor(() => {
      const calls = (fetchAuditQueue as any).mock.calls;
      const last = calls[calls.length - 1][0];
      expect(last.q).toBe("GMV");
      expect(last.page).toBe(1);
    });

    vi.useRealTimers();
  });

  it("showStatusFilter=true → 渲染 status 下拉, 选 canonical 后请求带 status", async () => {
    const { fetchAuditQueue } = await import("@/api");
    (fetchAuditQueue as any).mockResolvedValue({
      items: [], total: 0, page: 1, size: 20,
    });
    const { container } = render(<AuditQueue nsId={1} showStatusFilter />);

    await waitFor(() => expect(fetchAuditQueue).toHaveBeenCalledTimes(1));
    expect((fetchAuditQueue as any).mock.calls[0][0]).not.toHaveProperty("status");

    /* 等 Empty 出现 (loading=false) 防止 Spin 遮挡 mouseDown */
    await waitFor(() => expect(container.querySelector(".ant-empty")).toBeTruthy());

    /* status 下拉是 3 个 Select 中第 3 个 (类型/来源/状态), 搜索框是 Input.Search */
    const selectors = container.querySelectorAll(".ant-select-selector");
    expect(selectors.length).toBe(3);
    const statusSelector = selectors[2] as HTMLElement;
    fireEvent.mouseDown(statusSelector);

    /* "canonical" 文本在 popup 中 + 选中态 selection-item 都可能出现, 取 .ant-select-item-option 内 */
    const canonicalOption = await screen.findByText("canonical", {
      selector: ".ant-select-item-option-content, .ant-select-item-option-content *",
    });
    fireEvent.click(canonicalOption);

    await waitFor(() => {
      const calls = (fetchAuditQueue as any).mock.calls;
      const last = calls[calls.length - 1][0];
      expect(last.status).toBe("canonical");
    });
  });
});
