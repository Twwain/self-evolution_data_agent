import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect } from "vitest";
import { ToolNode } from "@/components/stream/ToolNode";
import { ToolTree } from "@/components/stream/ToolTree";

describe("ToolNode", () => {
  it("renders pending status with spinner role", () => {
    render(<ToolNode node={{ toolCallId: "t1", name: "lookup_knowledge", input: { q: "x" }, status: "pending" }} />);
    expect(screen.getByText("lookup_knowledge")).toBeInTheDocument();
    expect(screen.getByLabelText(/pending/i)).toBeInTheDocument();
  });
  it("expands input/output on click", async () => {
    const user = userEvent.setup();
    render(<ToolNode node={{ toolCallId: "t1", name: "fetch_collection_schema", input: { collection: "c_product" }, output: "fields: [a, b]", status: "ok" }} />);
    expect(screen.queryByText(/c_product/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /detail/i }));
    expect(screen.getByText(/c_product/)).toBeInTheDocument();
    expect(screen.getByText(/fields/)).toBeInTheDocument();
  });
  it("ToolTree returns null when idle with no tools (no noise)", () => {
    const { container } = render(<ToolTree tools={[]} status="idle" />);
    expect(container).toBeEmptyDOMElement();
  });
  it("ToolTree shows thinking hint when running with no tools", () => {
    render(<ToolTree tools={[]} status="running" />);
    expect(screen.getByText(/思考/)).toBeInTheDocument();
  });
});
