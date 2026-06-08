import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { CorrectionControls } from "@/components/stream/CorrectionControls";

describe("CorrectionControls", () => {
  it("abort fires onCorrect with empty instruction immediately", async () => {
    const fn = vi.fn();
    const user = userEvent.setup();
    render(<CorrectionControls disabled={false} onCorrect={fn} />);
    await user.click(screen.getByRole("button", { name: /abort/i }));
    expect(fn).toHaveBeenCalledWith("abort", "");
  });

  it("redirect opens modal, requires instruction text, then fires", async () => {
    const fn = vi.fn();
    const user = userEvent.setup();
    render(<CorrectionControls disabled={false} onCorrect={fn} />);
    await user.click(screen.getByRole("button", { name: /redirect/i }));
    const ta = await screen.findByRole("textbox");
    await user.type(ta, "换条思路");
    await user.click(screen.getByRole("button", { name: /confirm/i }));
    expect(fn).toHaveBeenCalledWith("redirect", "换条思路");
  });

  it("buttons disabled when disabled prop true", () => {
    render(<CorrectionControls disabled onCorrect={() => {}} />);
    expect(screen.getByRole("button", { name: /abort/i })).toBeDisabled();
  });
});
