import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConfidenceTag } from "./ConfidenceTag";

describe("ConfidenceTag", () => {
  it("renders blue for confirmed_by_introspect", () => {
    render(<ConfidenceTag status="confirmed_by_introspect" />);
    expect(screen.getByText("DBA 注释")).toBeInTheDocument();
  });

  it("renders green for confirmed_by_code", () => {
    render(<ConfidenceTag status="confirmed_by_code" />);
    expect(screen.getByText("代码确认")).toBeInTheDocument();
  });

  it("renders default for confirmed_by_user", () => {
    render(<ConfidenceTag status="confirmed_by_user" />);
    expect(screen.getByText("已确认")).toBeInTheDocument();
  });

  it("renders orange for evidence_only", () => {
    render(<ConfidenceTag status="evidence_only" />);
    expect(screen.getByText("需人工确认")).toBeInTheDocument();
  });

  it("returns null for unverified (no tag rendered)", () => {
    const { container } = render(<ConfidenceTag status="unverified" />);
    expect(container.firstChild).toBeNull();
  });
});
