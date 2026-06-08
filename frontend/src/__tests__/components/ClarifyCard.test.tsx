import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ClarifyCard } from "@/components/stream/ClarifyCard";

describe("ClarifyCard", () => {
  it("renders question + options + invokes onSubmit", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<ClarifyCard pending={{ pendingId: 5, question: "选哪个?", options: ["A", "B"] }} onSubmit={onSubmit} />);
    expect(screen.getByText("选哪个?")).toBeInTheDocument();
    await user.click(screen.getByText("A"));
    await user.click(screen.getByRole("button", { name: /submit/i }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(5, "A"));
  });
  it("supports free-text answer when no options", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    render(<ClarifyCard pending={{ pendingId: 7, question: "describe?" }} onSubmit={onSubmit} />);
    await user.type(screen.getByRole("textbox"), "my answer");
    await user.click(screen.getByRole("button", { name: /submit/i }));
    expect(onSubmit).toHaveBeenCalledWith(7, "my answer");
  });
});
