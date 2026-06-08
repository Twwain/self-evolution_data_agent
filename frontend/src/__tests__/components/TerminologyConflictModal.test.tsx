/* ════════════════════════════════════════════════════════════════════════════
 *  TerminologyConflictModal — Phase 3 Task 3.3 + manual_edit 扩展
 *  5 resolution_choice + side-by-side existing vs candidate + manual_edit 表单
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import TerminologyConflictModal from "@/components/audit/TerminologyConflictModal";
import * as api from "@/api";
import type { TerminologyConflict } from "@/types";

vi.mock("@/api", () => ({
  resolveTerminologyConflict: vi.fn(),
  getDatabases: vi.fn().mockResolvedValue({
    databases: [{ database: "db_q", db_type: "mongodb" }],
  }),
  getCollections: vi.fn().mockResolvedValue({
    collections: ["c_category"],
    db_type: "mongodb",
  }),
}));

const conflict: TerminologyConflict = {
  id: 1,
  namespace_id: 1,
  existing_entry_id: 42,
  candidate_payload: JSON.stringify({
    term: "订单",
    synonyms: ["单子"],
    primary_collection: "c_category",
    primary_database: "db_q",
    db_type: "mongodb",
    source_collections: ["c_category"],
  }),
  candidate_source: "git",
  candidate_repo_id: 7,
  status: "open",
  created_at: "2026-05-07T00:00:00Z",
};

const existing = {
  term: "商品",
  synonyms: ["货品"],
  primary_collection: "c_category",
  primary_database: "db_q",
  db_type: "mongodb",
  source_collections: ["c_category"],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.resolveTerminologyConflict as any).mockResolvedValue({
    id: 1,
    status: "resolved",
    choice: "keep_existing",
  });
});

describe("TerminologyConflictModal", () => {
  it.each(["keep_existing", "replace", "merge_both", "reject_both"] as const)(
    "choice=%s calls API and reports back via onClose",
    async (choice) => {
      const onClose = vi.fn();
      render(
        <TerminologyConflictModal
          conflict={conflict}
          existing={existing}
          open
          onClose={onClose}
        />,
      );
      const btn = screen.getByLabelText(`choice-${choice}`);
      fireEvent.click(btn);
      await waitFor(() => {
        expect(api.resolveTerminologyConflict).toHaveBeenCalledWith(1, 1, choice);
      });
      await waitFor(() => {
        expect(onClose).toHaveBeenCalledWith({ resolved: true, choice });
      });
    },
  );

  it("shows existing vs candidate side-by-side", () => {
    render(
      <TerminologyConflictModal
        conflict={conflict}
        existing={existing}
        open
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("商品")).toBeInTheDocument();
    expect(screen.getByText("订单")).toBeInTheDocument();
  });

  it("manual_edit click expands inline edit form (does not call API yet)", async () => {
    const onClose = vi.fn();
    render(
      <TerminologyConflictModal
        conflict={conflict}
        existing={existing}
        open
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByLabelText("choice-manual_edit"));
    // 表单展开
    await waitFor(() => {
      expect(screen.getByTestId("manual-edit-form")).toBeInTheDocument();
    });
    // 不应直接调 API — 等用户点保存才调
    expect(api.resolveTerminologyConflict).not.toHaveBeenCalled();
    // 4 个原 choice 按钮应该消失 (editMode 切换)
    expect(screen.queryByLabelText("choice-keep_existing")).toBeNull();
  });

  it("manual_edit save submits edited_payload to API", async () => {
    const onClose = vi.fn();
    render(
      <TerminologyConflictModal
        conflict={conflict}
        existing={existing}
        open
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByLabelText("choice-manual_edit"));
    await waitFor(() =>
      expect(screen.getByTestId("manual-edit-form")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByLabelText("manual-edit-save"));
    await waitFor(() => {
      expect(api.resolveTerminologyConflict).toHaveBeenCalledWith(
        1, 1, "manual_edit",
        expect.objectContaining({
          term: "商品",  // existing.term 兜底
          primary_collection: "c_category",  // 锁定路由
          primary_database: "db_q",
          db_type: "mongodb",
          synonyms: expect.arrayContaining(["货品", "订单", "单子"]),  // 并集预填
        }),
      );
    });
    await waitFor(() => {
      expect(onClose).toHaveBeenCalledWith({ resolved: true, choice: "manual_edit" });
    });
  });

  it("manual_edit cancel returns to choice buttons (no API call)", async () => {
    render(
      <TerminologyConflictModal
        conflict={conflict}
        existing={existing}
        open
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText("choice-manual_edit"));
    await waitFor(() =>
      expect(screen.getByTestId("manual-edit-form")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByLabelText("manual-edit-cancel"));
    await waitFor(() => {
      expect(screen.queryByTestId("manual-edit-form")).toBeNull();
    });
    expect(screen.getByLabelText("choice-keep_existing")).toBeInTheDocument();
    expect(api.resolveTerminologyConflict).not.toHaveBeenCalled();
  });
});
