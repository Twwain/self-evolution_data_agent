import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { EnumBindDrawer } from "./EnumBindDrawer";
import { enumApi } from "@/api";

vi.mock("@/api", () => ({
  enumApi: {
    listEnumDictionaries: vi.fn(),
    bindFieldEnum: vi.fn(),
  },
}));

const mockList = enumApi.listEnumDictionaries as ReturnType<typeof vi.fn>;
const mockBind = enumApi.bindFieldEnum as ReturnType<typeof vi.fn>;

describe("EnumBindDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockList.mockResolvedValue({
      items: [
        {
          id: 99,
          enum_class_name: "OrderStatus",
          values: [
            { name: "PENDING", db_value: 0 },
            { name: "PAID", db_value: 1 },
          ],
          source: "manual",
          status: "canonical",
        },
      ],
      total: 1,
    });
    mockBind.mockResolvedValue({ field: "status", enum_match_status: "matched" });
  });

  it("renders field info and sample values", () => {
    render(
      <EnumBindDrawer
        open
        collectionId={1}
        fieldName="status"
        fieldType="Integer"
        namespaceId={1}
        samples={[0, 1]}
        onClose={vi.fn()}
        onBound={vi.fn()}
      />,
    );
    expect(screen.getByText("绑定字段 status (Integer)")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("loads enum list on open", async () => {
    render(
      <EnumBindDrawer
        open
        collectionId={1}
        fieldName="status"
        fieldType="Integer"
        namespaceId={1}
        samples={[0, 1]}
        onClose={vi.fn()}
        onBound={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(mockList).toHaveBeenCalledWith({ namespace_id: 1 }),
    );
  });

  it("shows warning when samples not covered by selected enum", async () => {
    render(
      <EnumBindDrawer
        open
        collectionId={1}
        fieldName="status"
        fieldType="Integer"
        namespaceId={1}
        samples={[0, 1, 99]}
        onClose={vi.fn()}
        onBound={vi.fn()}
      />,
    );
    await waitFor(() => expect(mockList).toHaveBeenCalled());

    // Select the enum from dropdown
    const select = screen.getByRole("combobox");
    fireEvent.mouseDown(select);
    await waitFor(() => screen.getByTitle(/OrderStatus/));
    fireEvent.click(screen.getByTitle(/OrderStatus/));

    // Should show warning about uncovered samples
    await waitFor(() => screen.getByText(/未在 enum 值集合中/));
    expect(screen.getByRole("button", { name: /强制绑定/ })).toBeInTheDocument();
  });

  it("bind button is disabled when no enum selected", () => {
    render(
      <EnumBindDrawer
        open
        collectionId={1}
        fieldName="status"
        fieldType="Integer"
        namespaceId={1}
        samples={[0, 1]}
        onClose={vi.fn()}
        onBound={vi.fn()}
      />,
    );
    const bindBtn = screen.getByRole("button", { name: /绑.*定/ });
    expect(bindBtn).toBeDisabled();
  });

  it("force-bind invokes API with (namespaceId, collectionId, fieldName, { enum_dict_id, force })", async () => {
    const onBound = vi.fn();
    render(
      <EnumBindDrawer
        open
        collectionId={42}
        fieldName="status"
        fieldType="Integer"
        namespaceId={7}
        samples={[0, 1, 99]}
        onClose={vi.fn()}
        onBound={onBound}
      />,
    );
    await waitFor(() => expect(mockList).toHaveBeenCalled());

    const select = screen.getByRole("combobox");
    fireEvent.mouseDown(select);
    await waitFor(() => screen.getByTitle(/OrderStatus/));
    fireEvent.click(screen.getByTitle(/OrderStatus/));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /强制绑定/ })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /强制绑定/ }));

    await waitFor(() =>
      expect(mockBind).toHaveBeenCalledWith(7, 42, "status", {
        enum_dict_id: 99,
        force: true,
      }),
    );
  });
});
