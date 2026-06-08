import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { EnumEditorModal } from "./EnumEditorModal";

describe("EnumEditorModal", () => {
  const baseProps = {
    open: true,
    mode: "create" as const,
    namespaceId: 1,
    dbType: "mongodb" as const,
    onClose: vi.fn(),
    onSubmit: vi.fn(() => Promise.resolve()),
  };

  /** Ant Design renders okText="确定" as "确 定" (with space) in accessible name */
  const getOkButton = () => screen.getByRole("button", { name: /确.*定/ });

  it("renders empty form for create mode", () => {
    render(<EnumEditorModal {...baseProps} />);
    const input = screen.getByPlaceholderText("如 OrderStatus");
    expect(input).toHaveValue("");
  });

  it("disables submit when no values added", () => {
    render(<EnumEditorModal {...baseProps} />);
    expect(getOkButton()).toBeDisabled();
  });

  it("disables submit when name is empty even with values", () => {
    render(
      <EnumEditorModal
        {...baseProps}
        initial={{ enum_class_name: "", values: [{ name: "A", db_value: 1 }] }}
      />,
    );
    expect(getOkButton()).toBeDisabled();
  });

  it("enables submit when name and values are provided", () => {
    render(
      <EnumEditorModal
        {...baseProps}
        initial={{ enum_class_name: "Status", values: [{ name: "ACTIVE", db_value: 1 }] }}
      />,
    );
    expect(getOkButton()).not.toBeDisabled();
  });

  it("calls onSubmit with payload on save", async () => {
    const onSubmit = vi.fn(() => Promise.resolve());
    render(
      <EnumEditorModal
        {...baseProps}
        onSubmit={onSubmit}
        initial={{ enum_class_name: "DeleteStatus", values: [{ name: "DELETED", db_value: 1 }] }}
      />,
    );
    fireEvent.click(getOkButton());
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit).toHaveBeenCalledWith({
      enum_class_name: "DeleteStatus",
      values: [{ name: "DELETED", db_value: 1 }],
      comment: undefined,
    });
  });

  it("adds a new value row when 添加值 is clicked", () => {
    render(<EnumEditorModal {...baseProps} />);
    fireEvent.click(screen.getByRole("button", { name: "添加值" }));
    expect(screen.getByPlaceholderText("枚举名")).toBeInTheDocument();
  });

  it("disables enum_class_name in edit mode", () => {
    render(
      <EnumEditorModal
        {...baseProps}
        mode="edit"
        initial={{ enum_class_name: "X", values: [{ name: "A", db_value: 0 }] }}
      />,
    );
    const input = screen.getByDisplayValue("X");
    expect(input).toBeDisabled();
  });
});
