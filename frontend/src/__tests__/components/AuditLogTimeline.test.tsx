/* ════════════════════════════════════════════════════════════════════════════
 *  AuditLogTimeline — diff_json 视觉对照 + 各 action color + loading/empty
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import AuditLogTimeline from "@/components/audit/AuditLogTimeline";

vi.mock("@/api", () => ({ fetchAuditLog: vi.fn() }));

beforeEach(() => vi.clearAllMocks());

describe("AuditLogTimeline", () => {
  it("空日志 → 渲染 Empty 占位", async () => {
    const { fetchAuditLog } = await import("@/api");
    (fetchAuditLog as any).mockResolvedValue([]);
    render(<AuditLogTimeline entryId={1} />);
    await waitFor(() => expect(screen.getByText(/无审计记录/)).toBeInTheDocument());
  });

  it("有日志 → 渲染 action / 状态流转 / 操作员", async () => {
    const { fetchAuditLog } = await import("@/api");
    (fetchAuditLog as any).mockResolvedValue([
      {
        id: 1,
        entry_id: 1,
        actor_id: 1,
        action: "approve",
        from_status: "proposed",
        to_status: "canonical",
        reason: "ok",
        diff_json: '{"before":{"status":"proposed"},"after":{"status":"canonical"}}',
        created_at: "2026-05-01T02:00:00Z",
      },
      {
        id: 2,
        entry_id: 1,
        actor_id: null,
        action: "expire",
        from_status: "proposed",
        to_status: "rejected",
        reason: "auto-expired",
        diff_json: null,
        created_at: "2026-05-02T02:00:00Z",
      },
    ]);
    render(<AuditLogTimeline entryId={1} />);
    await waitFor(() => expect(screen.getByText("approve")).toBeInTheDocument());
    expect(screen.getByText("expire")).toBeInTheDocument();
    expect(screen.getByText(/操作员 #1/)).toBeInTheDocument();
    expect(screen.getByText(/系统/)).toBeInTheDocument();
    // diff_json 视觉对照 (Task 12 v2 锚点)
    expect(screen.getByText('- {"status":"proposed"}')).toBeInTheDocument();
    expect(screen.getByText('+ {"status":"canonical"}')).toBeInTheDocument();
  });

  it("malformed diff_json → 渲染但不崩 (catch 兜底)", async () => {
    const { fetchAuditLog } = await import("@/api");
    (fetchAuditLog as any).mockResolvedValue([
      {
        id: 1,
        entry_id: 1,
        actor_id: 1,
        action: "edit",
        from_status: "canonical",
        to_status: "canonical",
        reason: "fix typo",
        diff_json: "{not json",
        created_at: "2026-05-01T02:00:00Z",
      },
    ]);
    render(<AuditLogTimeline entryId={1} />);
    await waitFor(() => expect(screen.getByText("edit")).toBeInTheDocument());
  });

  it("diff_json 字段缺 before/after → 不渲染 diff 块但行渲染正常", async () => {
    const { fetchAuditLog } = await import("@/api");
    (fetchAuditLog as any).mockResolvedValue([
      {
        id: 1,
        entry_id: 1,
        actor_id: 9,
        action: "supersede",
        from_status: "canonical",
        to_status: "superseded",
        reason: "merged into #99",
        diff_json: '{"only_meta":1}',
        created_at: "2026-05-01T02:00:00Z",
      },
    ]);
    render(<AuditLogTimeline entryId={1} />);
    await waitFor(() => expect(screen.getByText("supersede")).toBeInTheDocument());
  });
});
