import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ThinkingBlock } from "@/components/stream/ThinkingBlock";
import { KnowledgeProposedToast } from "@/components/stream/KnowledgeProposedToast";
import { CostWarningBanner } from "@/components/stream/CostWarningBanner";

describe("ThinkingBlock", () => {
  it("renders text inside collapsible panel", () => {
    render(<ThinkingBlock text="思考中..." />);
    expect(screen.getByText(/思考中/)).toBeInTheDocument();
  });
  it("hides when text empty", () => {
    const { container } = render(<ThinkingBlock text="" />);
    expect(container.firstChild).toBeNull();
  });
});

describe("KnowledgeProposedToast", () => {
  it("shows entry_type + preview", () => {
    render(
      <KnowledgeProposedToast
        items={[{ entryId: 1, entryType: "route_hint", preview: "订单=c_product" }]}
      />
    );
    expect(screen.getByText(/route_hint/)).toBeInTheDocument();
    expect(screen.getByText(/订单/)).toBeInTheDocument();
  });
});

describe("CostWarningBanner", () => {
  it("formats large numbers + shows advice", () => {
    render(
      <CostWarningBanner
        warnings={[{ estimatedDocs: 9_999_999, threshold: 5_000_000, advice: "use clarify" }]}
      />
    );
    expect(screen.getByText(/9,999,999/)).toBeInTheDocument();
    expect(screen.getByText(/use clarify/)).toBeInTheDocument();
  });
});
