import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { PendingEnumBindingTable } from "./PendingEnumBindingTable";
import { enumApi } from "@/api";

vi.mock("@/api", () => ({
  enumApi: {
    listPendingEnumBindings: vi.fn(),
    listEnumDictionaries: vi.fn(),
    bindFieldEnum: vi.fn(),
  },
}));

const mockPending = enumApi.listPendingEnumBindings as ReturnType<typeof vi.fn>;

describe("PendingEnumBindingTable", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // 字段名必须与后端 schema_canonical_v2.py:570-575 一致 —
    // 测 fixture 编造名字 = 测试在记录虚构 (api-contract-testing skill Layer 1).
    mockPending.mockResolvedValue({
      items: [
        {
          collection_id: 1,
          collection_name: "orders",
          field: "status",
          field_type: "Integer",
          enum_class_hint: "OrderStatus",
          sample_values: [0, 1, 2],
        },
        {
          collection_id: 2,
          collection_name: "users",
          field: "role",
          field_type: "String",
          enum_class_hint: null,
          sample_values: ["admin", "user"],
        },
      ],
      total: 2,
    });
  });

  it("lists pending fields from API", async () => {
    render(<PendingEnumBindingTable namespaceId={1} />);
    await waitFor(() => screen.getByText("orders"));
    expect(screen.getByText("status")).toBeInTheDocument();
    expect(screen.getByText("users")).toBeInTheDocument();
    expect(screen.getByText("role")).toBeInTheDocument();
  });

  it("shows hint tag when present", async () => {
    render(<PendingEnumBindingTable namespaceId={1} />);
    await waitFor(() => screen.getByText("OrderStatus"));
    expect(screen.getByText("OrderStatus")).toBeInTheDocument();
  });

  it("shows sample values as tags", async () => {
    render(<PendingEnumBindingTable namespaceId={1} />);
    await waitFor(() => screen.getByText("admin"));
    expect(screen.getByText("user")).toBeInTheDocument();
  });

  it("calls listPendingEnumBindings with namespace_id", async () => {
    render(<PendingEnumBindingTable namespaceId={42} />);
    await waitFor(() =>
      expect(mockPending).toHaveBeenCalledWith(42),
    );
  });
});
