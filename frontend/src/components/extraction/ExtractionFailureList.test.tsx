import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ExtractionFailureList } from "./ExtractionFailureList";

vi.mock("@/api", () => ({
  schemaCanonicalApi: {
    listExtractionFailures: vi.fn().mockResolvedValue([
      {
        id: 1, extraction_kind: "mybatis_example", failure_type: "unknown_table",
        source_mapper: "OrderMapper", source_method: "selectArchived",
        source_content: "SELECT * FROM t_order_archive",
        source_file: null,
        retry_count: 0, last_seen_at: "2026-05-15", created_at: "2026-05-15",
        failure_message: "Table 't_order_archive' doesn't exist",
      },
      {
        id: 2, extraction_kind: "enum_class", failure_type: "ast_parse_error",
        source_file: "BadEnum.java", source_mapper: null, source_method: null,
        source_content: null,
        retry_count: 999,
        last_seen_at: "2026-05-15", created_at: "2026-05-15",
        failure_message: "Java syntax not supported",
      },
    ]),
    retryExtractionFailure: vi.fn().mockResolvedValue({ ok: true }),
    ignoreExtractionFailure: vi.fn().mockResolvedValue({ ok: true }),
  },
}));

describe("ExtractionFailureList", () => {
  it("groups failures by extraction_kind", async () => {
    render(<ExtractionFailureList namespaceId={1} />);
    await waitFor(() => expect(screen.getByText(/mybatis_example/)).toBeInTheDocument());
    expect(screen.getByText(/enum_class/)).toBeInTheDocument();
  });

  it("renders failure_type + source detail", async () => {
    render(<ExtractionFailureList namespaceId={1} />);
    await waitFor(() => expect(screen.getByText(/unknown_table/)).toBeInTheDocument());
    expect(screen.getByText(/OrderMapper\.selectArchived/)).toBeInTheDocument();
  });

  it("clicks 重试 invokes API", async () => {
    const { schemaCanonicalApi } = await import("@/api");
    render(<ExtractionFailureList namespaceId={1} />);
    await waitFor(() => screen.getAllByRole("button", { name: /重.*试/ }));
    fireEvent.click(screen.getAllByRole("button", { name: /重.*试/ })[0]);
    await waitFor(() => expect(schemaCanonicalApi.retryExtractionFailure).toHaveBeenCalledWith(1));
  });

  it("clicks 忽略 invokes ignore API", async () => {
    const { schemaCanonicalApi } = await import("@/api");
    render(<ExtractionFailureList namespaceId={1} />);
    await waitFor(() => screen.getAllByRole("button", { name: /忽.*略/ }));
    fireEvent.click(screen.getAllByRole("button", { name: /忽.*略/ })[0]);
    await waitFor(() => expect(schemaCanonicalApi.ignoreExtractionFailure).toHaveBeenCalledWith(1));
  });

  it("disables 重试 when retry_count=999", async () => {
    render(<ExtractionFailureList namespaceId={1} />);
    await waitFor(() => screen.getAllByRole("button", { name: /重.*试/ }));
    const buttons = screen.getAllByRole("button", { name: /重.*试/ });
    // Second item has retry_count=999
    expect(buttons[1]).toBeDisabled();
  });
});
