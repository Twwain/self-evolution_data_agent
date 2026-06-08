/* ════════════════════════════════════════════════════════════════════════════
 *  EditCanonicalForm — terminology mode (Phase 3 Task 3.2)
 *  联动: db_type 自动同步 / collection 重置 / db_type 锁定 / term shape 校验
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import EditCanonicalForm from "@/components/audit/EditCanonicalForm";
import type { KnowledgeEntry } from "@/types";

vi.mock("@/api", () => ({
  editKnowledge: vi.fn().mockResolvedValue({}),
  previewConflict: vi.fn().mockResolvedValue({ conflicts: [] }),
  getDatabases: vi.fn(),
  getCollections: vi.fn(),
}));

const makeEntry = (payload: Record<string, any>): KnowledgeEntry =>
  ({
    id: 42,
    namespace_id: 1,
    entry_type: "terminology",
    tier: "normal",
    content: JSON.stringify(payload),
    raw_input: "",
    description: "",
    source: "manual",
    status: "canonical",
    is_superseded: false,
    refined_at: null,
    created_at: "2026-05-01T00:00:00Z",
  } as unknown as KnowledgeEntry);

const baseEntry = makeEntry({
  term: "商品",
  primary_database: "",
  primary_collection: "",
  synonyms: [],
});

beforeEach(async () => {
  vi.clearAllMocks();
  const api = await import("@/api");
  (api.getDatabases as any).mockResolvedValue({
    databases: [
      { database: "db_q", db_type: "mongodb", datasource_id: 2, host: "localhost" },
      { database: "db_main", db_type: "mysql", datasource_id: 1, host: "localhost" },
    ],
  });
  (api.getCollections as any).mockImplementation((_: number, database: string) => {
    if (database === "db_q") {
      return Promise.resolve({
        database: "db_q",
        db_type: "mongodb",
        collections: ["c_category", "c_product"],
      });
    }
    return Promise.resolve({
      database,
      db_type: "mysql",
      collections: ["t_user"],
    });
  });
  (api.previewConflict as any).mockResolvedValue({ conflicts: [] });
});

describe("EditCanonicalForm — terminology mode", () => {
  // ── antd Select 在 jsdom 中需 mouseDown 触发 open ──
  const openSelect = async (label: string) => {
    const inputs = await screen.findAllByLabelText(label);
    // ── input 元素永远是含 .ant-select-selector 祖先的那个 ──
    const selectInput = inputs.find((el) => el.closest(".ant-select-selector"));
    if (!selectInput) throw new Error(`no antd Select input for ${label}`);
    const selector = selectInput.closest(".ant-select-selector")!;
    fireEvent.mouseDown(selector);
  };

  it("selecting database auto-fills db_type readonly", async () => {
    render(<EditCanonicalForm entry={baseEntry} />);
    await waitFor(() => expect(screen.getAllByLabelText("database").length).toBeGreaterThan(0));
    await openSelect("database");
    const opt = await screen.findByText("db_q (mongodb)");
    fireEvent.click(opt);
    await waitFor(() => {
      const dbType = screen.getByLabelText("db_type") as HTMLInputElement;
      expect(dbType.value).toBe("mongodb");
      expect(dbType.disabled).toBe(true);
    });
  });

  it("selecting database triggers GET /collections", async () => {
    const api = await import("@/api");
    render(<EditCanonicalForm entry={baseEntry} />);
    await waitFor(() => expect(screen.getAllByLabelText("database").length).toBeGreaterThan(0));
    await openSelect("database");
    fireEvent.click(await screen.findByText("db_q (mongodb)"));
    await waitFor(() => expect(api.getCollections).toHaveBeenCalledWith(1, "db_q"));
  });

  it("changing database resets collection field", async () => {
    const filled = makeEntry({
      term: "订单",
      primary_database: "db_q",
      primary_collection: "c_category",
      db_type: "mongodb",
      synonyms: [],
    });
    render(<EditCanonicalForm entry={filled} />);

    // pre-condition: collection antd Select 把 selected value 渲染成 selection-item 文本
    await waitFor(() => {
      expect(screen.getByText("c_category")).toBeInTheDocument();
    });

    await openSelect("database");
    fireEvent.click(await screen.findByText("db_main (mysql)"));

    // ── collection 重置 → 选中文本不再可见 ──
    await waitFor(() => {
      expect(screen.queryByText("c_category")).not.toBeInTheDocument();
    });
  });

  it("user cannot manually edit db_type", async () => {
    render(<EditCanonicalForm entry={baseEntry} />);
    const dbType = (await screen.findByLabelText("db_type")) as HTMLInputElement;
    expect(dbType.disabled).toBe(true);
  });

  it("term shape validation shows error on too-long input", async () => {
    const user = userEvent.setup();
    render(<EditCanonicalForm entry={baseEntry} />);
    const term = (await screen.findByLabelText("term")) as HTMLInputElement;
    await user.clear(term);
    await user.type(term, "试".repeat(25));
    term.blur();
    await waitFor(() => {
      expect(
        screen.getByText(/不能超过|过长|单一业务名词/),
      ).toBeInTheDocument();
    });
  });
});
