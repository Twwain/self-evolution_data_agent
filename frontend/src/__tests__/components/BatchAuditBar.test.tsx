/* ════════════════════════════════════════════════════════════════════════════
 *  BatchAuditBar — 批量通过/拒绝 + confirm_token 二次确认 + BatchProgress
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import BatchAuditBar from "@/components/audit/BatchAuditBar";

vi.mock("@/api", () => ({ batchAudit: vi.fn() }));

beforeEach(() => vi.clearAllMocks());

describe("BatchAuditBar", () => {
  it("已选 N 条文案 + 通过/拒绝按钮", () => {
    render(<BatchAuditBar entryIds={[1, 2, 3]} />);
    expect(screen.getByText("已选 3 条")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批量通过" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批量拒绝" })).toBeInTheDocument();
  });

  it("批量通过 → 调 batchAudit + onDone 回调 + BatchProgress 显示", async () => {
    const { batchAudit } = await import("@/api");
    (batchAudit as any).mockResolvedValue({ affected_count: 2, success_ids: [1, 2] });
    const onDone = vi.fn();
    const user = userEvent.setup();
    render(<BatchAuditBar entryIds={[1, 2]} onDone={onDone} />);
    await user.click(screen.getByRole("button", { name: "批量通过" }));
    await waitFor(() =>
      expect(batchAudit).toHaveBeenCalledWith(
        [
          { entry_id: 1, action: "approve" },
          { entry_id: 2, action: "approve" },
        ],
        undefined,
      ),
    );
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("批量拒绝 → prompt 返 null 不触发 API", async () => {
    const { batchAudit } = await import("@/api");
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue(null);
    const user = userEvent.setup();
    render(<BatchAuditBar entryIds={[1]} />);
    await user.click(screen.getByRole("button", { name: "批量拒绝" }));
    expect(batchAudit).not.toHaveBeenCalled();
    promptSpy.mockRestore();
  });

  it("批量拒绝 → prompt 给 reason → 调 API 含 reason", async () => {
    const { batchAudit } = await import("@/api");
    (batchAudit as any).mockResolvedValue({ affected_count: 1, success_ids: [1] });
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("批量拒绝原因");
    const user = userEvent.setup();
    render(<BatchAuditBar entryIds={[1]} />);
    await user.click(screen.getByRole("button", { name: "批量拒绝" }));
    await waitFor(() =>
      expect(batchAudit).toHaveBeenCalledWith(
        [{ entry_id: 1, action: "reject", reason: "批量拒绝原因" }],
        undefined,
      ),
    );
    promptSpy.mockRestore();
  });

  it("API 错误 (非 confirm_token_required) → catch + message.error", async () => {
    const { batchAudit } = await import("@/api");
    (batchAudit as any).mockRejectedValue({ response: { data: { detail: "boom" } } });
    const user = userEvent.setup();
    render(<BatchAuditBar entryIds={[1, 2]} />);
    await user.click(screen.getByRole("button", { name: "批量通过" }));
    await waitFor(() => expect(batchAudit).toHaveBeenCalled());
    // failed 列表填充 — Tag "failed: 2"
    await waitFor(() =>
      expect(screen.getByText(/failed: 2/)).toBeInTheDocument(),
    );
  });
});
