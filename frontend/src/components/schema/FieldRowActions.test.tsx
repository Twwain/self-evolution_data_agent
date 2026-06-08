import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FieldRowActions } from "./FieldRowActions";

describe("FieldRowActions", () => {
  it("renders 3 icon buttons", () => {
    render(<FieldRowActions onEvidence={vi.fn()} onHistory={vi.fn()} onLock={vi.fn()} userLocked={false} />);
    expect(screen.getByLabelText("证据")).toBeInTheDocument();
    expect(screen.getByLabelText("历史")).toBeInTheDocument();
    expect(screen.getByLabelText("锁定")).toBeInTheDocument();
  });

  it("shows unlock label when userLocked", () => {
    render(<FieldRowActions onEvidence={vi.fn()} onHistory={vi.fn()} onLock={vi.fn()} userLocked={true} />);
    expect(screen.getByLabelText("解锁")).toBeInTheDocument();
  });

  it("invokes onEvidence callback on click", () => {
    const onEvidence = vi.fn();
    render(<FieldRowActions onEvidence={onEvidence} onHistory={vi.fn()} onLock={vi.fn()} userLocked={false} />);
    fireEvent.click(screen.getByLabelText("证据"));
    expect(onEvidence).toHaveBeenCalled();
  });

  it("invokes onHistory callback on click", () => {
    const onHistory = vi.fn();
    render(<FieldRowActions onEvidence={vi.fn()} onHistory={onHistory} onLock={vi.fn()} userLocked={false} />);
    fireEvent.click(screen.getByLabelText("历史"));
    expect(onHistory).toHaveBeenCalled();
  });

  it("invokes onLock callback on click", () => {
    const onLock = vi.fn();
    render(<FieldRowActions onEvidence={vi.fn()} onHistory={vi.fn()} onLock={onLock} userLocked={false} />);
    fireEvent.click(screen.getByLabelText("锁定"));
    expect(onLock).toHaveBeenCalled();
  });
});
