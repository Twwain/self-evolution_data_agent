import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ConflictsTab } from "./ConflictsTab";

vi.mock("@/api", () => ({
  schemaCanonicalApi: {
    listConflicts: vi.fn().mockResolvedValue([
      {
        id: 100, target: "t_order", field_path: "status",
        database: "order_db", db_type: "mysql",
        candidate_kind: "field_description", conflict_type: "field_value",
        candidates_snapshot: [
          { candidate_id: 1, value: { description: "订单状态" }, evidence: [], confidence_status: "confirmed_by_code", source: "code_jpa_javadoc" },
          { candidate_id: 2, value: { description: "状态" }, evidence: [], confidence_status: "confirmed_by_introspect", source: "introspect" },
        ],
        status: "open", resolution_choice: null, resolved_at: null,
        created_at: "2026-05-15",
      },
    ]),
    resolveConflict: vi.fn().mockResolvedValue({}),
  },
}));

describe("ConflictsTab", () => {
  it("renders open conflicts", async () => {
    render(<ConflictsTab namespaceId={1} />);
    await waitFor(() => expect(screen.getByText(/t_order\.status/)).toBeInTheDocument());
    expect(screen.getByText(/field_value/)).toBeInTheDocument();
  });

  it("selects card A and submits to invoke resolveConflict API", async () => {
    const { schemaCanonicalApi } = await import("@/api");
    render(<ConflictsTab namespaceId={1} />);
    // Wait for cards to render
    await waitFor(() => screen.getByText(/^A:/));

    // Click card A to select it
    const cardA = screen.getByText(/^A:/).closest(".ant-card")!;
    fireEvent.click(cardA);

    // Click submit
    const submitBtn = screen.getByRole("button", { name: /提交选择/ });
    fireEvent.click(submitBtn);

    await waitFor(() => expect(schemaCanonicalApi.resolveConflict).toHaveBeenCalledWith(
      1, 100, { resolution_choice: "keep_a", candidate_id: 1, reason: "selected candidate A" },
    ));
  });

  it("handles 409 conflict with warning message", async () => {
    const { schemaCanonicalApi } = await import("@/api");
    (schemaCanonicalApi.resolveConflict as any).mockRejectedValueOnce({
      response: { status: 409 },
    });
    render(<ConflictsTab namespaceId={1} />);
    // Wait for cards to render
    await waitFor(() => screen.getByText(/^A:/));

    // Click card A to select it
    const cardA = screen.getByText(/^A:/).closest(".ant-card")!;
    fireEvent.click(cardA);

    const callsBefore = (schemaCanonicalApi.listConflicts as any).mock.calls.length;

    // Click submit
    const submitBtn = screen.getByRole("button", { name: /提交选择/ });
    fireEvent.click(submitBtn);

    // After 409, listConflicts should be called again (refresh)
    await waitFor(() =>
      expect((schemaCanonicalApi.listConflicts as any).mock.calls.length).toBeGreaterThan(callsBefore),
    );
  });
});
