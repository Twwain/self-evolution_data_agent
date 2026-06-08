import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import BatchProgress from "@/components/audit/BatchProgress";

describe("BatchProgress", () => {
  it("renders 70% percent + done/total + failed tag", () => {
    const { container } = render(
      <BatchProgress total={10} done={7} failedIds={[3]} />,
    );
    expect(screen.getByText("7/10")).toBeInTheDocument();
    expect(screen.getByText("failed: 1")).toBeInTheDocument();
    // antd Progress writes percent into aria
    const bar = container.querySelector(".ant-progress");
    expect(bar).toBeTruthy();
    expect(container.textContent).toContain("70");
  });

  it("returns null when total=0 (hidden)", () => {
    const { container } = render(
      <BatchProgress total={0} done={0} failedIds={[]} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
