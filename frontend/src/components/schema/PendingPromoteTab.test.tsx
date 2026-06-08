import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PendingPromoteTab } from "./PendingPromoteTab";

vi.mock("@/api", () => ({
  schemaCanonicalApi: {
    listPendingCandidates: vi.fn().mockResolvedValue([
      {
        id: 10, target: "t_order", field_path: "status", candidate_kind: "field_description",
        candidates: [
          { id: 100, source: "code_jpa_javadoc", value: { description: "订单状态" } },
          { id: 101, source: "introspect", value: { description: "状态" } },
        ],
      },
    ]),
    promote: vi.fn().mockResolvedValue({
      promoted_count: 5, conflicted_count: 1, candidates_processed: 6,
      skipped_user_locked: 0, skipped_in_conflict: 0, duration_seconds: 0.5,
    }),
  },
}));

describe("PendingPromoteTab", () => {
  it("renders pending candidate aggregations", async () => {
    render(<PendingPromoteTab namespaceId={1} />);
    await waitFor(() => expect(screen.getByText(/t_order\.status/)).toBeInTheDocument());
    expect(screen.getByText(/2 个候选/)).toBeInTheDocument();
  });

  it("clicks 立即汇聚 triggers promote API + shows report", async () => {
    const { schemaCanonicalApi } = await import("@/api");
    render(<PendingPromoteTab namespaceId={1} />);
    await waitFor(() => screen.getByRole("button", { name: /立即汇聚/ }));
    fireEvent.click(screen.getByRole("button", { name: /立即汇聚/ }));
    await waitFor(() => expect(schemaCanonicalApi.promote).toHaveBeenCalledWith(1));
    expect(await screen.findByText(/promoted: 5/)).toBeInTheDocument();
  });
});
