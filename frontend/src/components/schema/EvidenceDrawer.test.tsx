import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { EvidenceDrawer } from "./EvidenceDrawer";

const EVIDENCE_DATA = {
  field_path: "status",
  candidates: [
    {
      id: 1,
      candidate_value: { description: "订单状态" },
      evidence_sources: [
        { source: "code_jpa_javadoc", file: "OrderEntity.java", line: 42, repo_url: "https://x" },
        { source: "introspect", extra: { COLUMN_COMMENT: "状态" } },
      ],
      confidence_status: "evidence_only" as const,
      status: "pending",
    },
  ],
  canonical_value: { description: "订单状态" },
};

vi.mock("@/api", () => ({
  schemaCanonicalApi: {
    getSchemaEvidence: vi.fn(),
    confirmField: vi.fn(),
  },
}));

import { schemaCanonicalApi } from "@/api";

describe("EvidenceDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (schemaCanonicalApi.getSchemaEvidence as any).mockResolvedValue(EVIDENCE_DATA);
    (schemaCanonicalApi.confirmField as any).mockResolvedValue({ ok: true });
  });

  it("renders evidence sources", async () => {
    render(<EvidenceDrawer namespaceId={1} scoId={10} fieldPath="status" open={true} onClose={vi.fn()} />);
    expect(await screen.findByText(/code_jpa_javadoc/)).toBeInTheDocument();
    expect(screen.getByText(/OrderEntity.java:42/)).toBeInTheDocument();
    expect(screen.getByText(/introspect/)).toBeInTheDocument();
  });

  it("clicks 确认 invokes confirmField API", async () => {
    render(<EvidenceDrawer namespaceId={1} scoId={10} fieldPath="status" open={true} onClose={vi.fn()} />);
    // Wait for content to load
    await screen.findByText(/code_jpa_javadoc/);
    const btn = screen.getByRole("button", { name: /确.*认/ });
    fireEvent.click(btn);
    await waitFor(() => expect(schemaCanonicalApi.confirmField).toHaveBeenCalledWith(
      1, 10, { field_path: "status", action: "confirm" },
    ));
  });

  it("clicks 修正 opens edit form", async () => {
    render(<EvidenceDrawer namespaceId={1} scoId={10} fieldPath="status" open={true} onClose={vi.fn()} />);
    await screen.findByText(/code_jpa_javadoc/);
    const btn = screen.getByRole("button", { name: /修.*正/ });
    fireEvent.click(btn);
    expect(await screen.findByLabelText(/新值/)).toBeInTheDocument();
  });

  it("clicks 忽略 sends ignore action", async () => {
    render(<EvidenceDrawer namespaceId={1} scoId={10} fieldPath="status" open={true} onClose={vi.fn()} />);
    await screen.findByText(/code_jpa_javadoc/);
    const btn = screen.getByRole("button", { name: /忽.*略/ });
    fireEvent.click(btn);
    await waitFor(() => expect(schemaCanonicalApi.confirmField).toHaveBeenCalledWith(
      1, 10, { field_path: "status", action: "ignore" },
    ));
  });
});
