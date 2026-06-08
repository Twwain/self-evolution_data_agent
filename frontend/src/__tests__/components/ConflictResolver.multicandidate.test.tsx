/**
 * Unit tests for ConflictResolver multi-candidate card selection and edit mode.
 *
 * **Validates: Requirements 2.5, 2.7**
 *
 * Covers:
 * - Multi-candidate (>2) renders all candidates as selectable cards
 * - Card selection highlights and enables submit
 * - Edit mode with TextArea
 * - Negative-path validation: empty/whitespace blocked, valid content succeeds
 * - 2-candidate conflicts preserve A/B button behavior
 */

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ConflictResolver } from "@/components/schema/ConflictResolver";
import type { SchemaConflict } from "@/types/schema-canonical";

// ════════════════════════════════════════════════════════════════
//  Fixtures
// ════════════════════════════════════════════════════════════════

function makeConflict(candidateCount: number, overrides?: Partial<SchemaConflict>): SchemaConflict {
  const candidates = Array.from({ length: candidateCount }, (_, i) => ({
    candidate_id: i + 1,
    value: { description: `Candidate ${i + 1} description` },
    evidence: [],
    confidence_status: "confirmed_by_code",
  }));

  return {
    id: 1,
    db_type: "mysql",
    database: "test_db",
    target: "t_user",
    field_path: "name",
    candidate_kind: "field_description",
    conflict_type: "value_conflict",
    candidates_snapshot: candidates,
    status: "open",
    resolution_choice: null,
    resolved_at: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

// ════════════════════════════════════════════════════════════════
//  Multi-candidate rendering
// ════════════════════════════════════════════════════════════════

describe("ConflictResolver multi-candidate card selection", () => {
  it("renders all candidates as cards with unique labels when >2 candidates", () => {
    const conflict = makeConflict(3);
    const onResolve = vi.fn();
    render(<ConflictResolver conflict={conflict} onResolve={onResolve} />);

    // All 3 candidates should be rendered with labels A, B, C
    expect(screen.getByText(/^A:/)).toBeInTheDocument();
    expect(screen.getByText(/^B:/)).toBeInTheDocument();
    expect(screen.getByText(/^C:/)).toBeInTheDocument();

    // Should NOT render "保留 A" / "保留 B" buttons
    expect(screen.queryByRole("button", { name: /保留\s*A/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /保留\s*B/ })).toBeNull();
  });

  it("highlights selected card and shows edit button", () => {
    const conflict = makeConflict(3);
    const onResolve = vi.fn();
    render(<ConflictResolver conflict={conflict} onResolve={onResolve} />);

    // Click on the second card (B)
    const cardB = screen.getByText(/^B:/).closest(".ant-card")!;
    fireEvent.click(cardB);

    // Edit button should appear on selected card (Ant Design inserts space between CJK chars)
    expect(screen.getByRole("button", { name: /编\s*辑/ })).toBeInTheDocument();
  });

  it("submit without edit calls onResolve with keep_a and candidate_id", () => {
    const conflict = makeConflict(3);
    const onResolve = vi.fn();
    render(<ConflictResolver conflict={conflict} onResolve={onResolve} />);

    // Select card A
    const cardA = screen.getByText(/^A:/).closest(".ant-card")!;
    fireEvent.click(cardA);

    // Submit
    const submitBtn = screen.getByRole("button", { name: /提交选择/ });
    fireEvent.click(submitBtn);

    expect(onResolve).toHaveBeenCalledWith(
      expect.objectContaining({
        resolution_choice: "keep_a",
        candidate_id: conflict.candidates_snapshot[0].candidate_id,
      }),
    );
  });

  it("submit with edit calls onResolve with merge and edited value", () => {
    const conflict = makeConflict(3);
    const onResolve = vi.fn();
    render(<ConflictResolver conflict={conflict} onResolve={onResolve} />);

    // Select card A
    const cardA = screen.getByText(/^A:/).closest(".ant-card")!;
    fireEvent.click(cardA);

    // Click edit (Ant Design inserts space between CJK chars)
    const editBtn = screen.getByRole("button", { name: /编\s*辑/ });
    fireEvent.click(editBtn);

    // Change value to valid JSON content
    const textarea = screen.getByLabelText("编辑候选值");
    const newValue = '{"description": "Updated value"}';
    fireEvent.change(textarea, { target: { value: newValue } });

    // Submit
    const submitBtn = screen.getByRole("button", { name: /提交选择/ });
    fireEvent.click(submitBtn);

    expect(onResolve).toHaveBeenCalledWith({
      resolution_choice: "merge",
      resolution_value: { description: "Updated value" },
    });
  });
});

// ════════════════════════════════════════════════════════════════
//  Negative-path validation
// ════════════════════════════════════════════════════════════════

describe("ConflictResolver negative-path validation", () => {
  it("blocks submission when editing and editValue is empty string", () => {
    const conflict = makeConflict(3);
    const onResolve = vi.fn();
    render(<ConflictResolver conflict={conflict} onResolve={onResolve} />);

    // Select card A
    const cardA = screen.getByText(/^A:/).closest(".ant-card")!;
    fireEvent.click(cardA);

    // Click edit (Ant Design inserts space between CJK chars)
    const editBtn = screen.getByRole("button", { name: /编\s*辑/ });
    fireEvent.click(editBtn);

    // Clear the textarea to empty
    const textarea = screen.getByLabelText("编辑候选值");
    fireEvent.change(textarea, { target: { value: "" } });

    // Submit button should be disabled
    const submitBtn = screen.getByRole("button", { name: /提交选择/ });
    expect(submitBtn).toBeDisabled();

    // Click should not trigger onResolve
    fireEvent.click(submitBtn);
    expect(onResolve).not.toHaveBeenCalled();
  });

  it("blocks submission when editing and editValue is whitespace-only", () => {
    const conflict = makeConflict(3);
    const onResolve = vi.fn();
    render(<ConflictResolver conflict={conflict} onResolve={onResolve} />);

    // Select card A
    const cardA = screen.getByText(/^A:/).closest(".ant-card")!;
    fireEvent.click(cardA);

    // Click edit (Ant Design inserts space between CJK chars)
    const editBtn = screen.getByRole("button", { name: /编\s*辑/ });
    fireEvent.click(editBtn);

    // Set whitespace-only value
    const textarea = screen.getByLabelText("编辑候选值");
    fireEvent.change(textarea, { target: { value: "   \n\t  " } });

    // Submit button should be disabled
    const submitBtn = screen.getByRole("button", { name: /提交选择/ });
    expect(submitBtn).toBeDisabled();

    // Click should not trigger onResolve
    fireEvent.click(submitBtn);
    expect(onResolve).not.toHaveBeenCalled();
  });

  it("allows submission when editing and editValue has valid content", () => {
    const conflict = makeConflict(3);
    const onResolve = vi.fn();
    render(<ConflictResolver conflict={conflict} onResolve={onResolve} />);

    // Select card A
    const cardA = screen.getByText(/^A:/).closest(".ant-card")!;
    fireEvent.click(cardA);

    // Click edit (Ant Design inserts space between CJK chars)
    const editBtn = screen.getByRole("button", { name: /编\s*辑/ });
    fireEvent.click(editBtn);

    // Set valid JSON content
    const textarea = screen.getByLabelText("编辑候选值");
    fireEvent.change(textarea, { target: { value: '{"description": "Valid content"}' } });

    // Submit button should be enabled
    const submitBtn = screen.getByRole("button", { name: /提交选择/ });
    expect(submitBtn).not.toBeDisabled();

    // Click should trigger onResolve
    fireEvent.click(submitBtn);
    expect(onResolve).toHaveBeenCalledWith({
      resolution_choice: "merge",
      resolution_value: { description: "Valid content" },
    });
  });

  it("shows JSON validation error for invalid JSON input", () => {
    const conflict = makeConflict(3);
    const onResolve = vi.fn();
    render(<ConflictResolver conflict={conflict} onResolve={onResolve} />);

    // Select card A
    const cardA = screen.getByText(/^A:/).closest(".ant-card")!;
    fireEvent.click(cardA);

    // Click edit (Ant Design inserts space between CJK chars)
    const editBtn = screen.getByRole("button", { name: /编\s*辑/ });
    fireEvent.click(editBtn);

    // Set invalid JSON
    const textarea = screen.getByLabelText("编辑候选值");
    fireEvent.change(textarea, { target: { value: "{invalid json" } });

    // Should show validation error
    expect(screen.getByText("输入内容不是有效的 JSON 格式")).toBeInTheDocument();

    // Submit button should be disabled
    const submitBtn = screen.getByRole("button", { name: /提交选择/ });
    expect(submitBtn).toBeDisabled();
  });

  it("submit button is disabled when no card is selected", () => {
    const conflict = makeConflict(3);
    const onResolve = vi.fn();
    render(<ConflictResolver conflict={conflict} onResolve={onResolve} />);

    // Submit button should be disabled without selection
    const submitBtn = screen.getByRole("button", { name: /提交选择/ });
    expect(submitBtn).toBeDisabled();
  });
});

// ════════════════════════════════════════════════════════════════
//  2-candidate unified card selection
// ════════════════════════════════════════════════════════════════

describe("ConflictResolver 2-candidate unified card selection", () => {
  it("renders selectable cards (not A/B buttons) for 2-candidate conflicts", () => {
    const conflict = makeConflict(2);
    const onResolve = vi.fn();
    render(<ConflictResolver conflict={conflict} onResolve={onResolve} />);

    // Should render cards with labels A, B
    expect(screen.getByText(/^A:/)).toBeInTheDocument();
    expect(screen.getByText(/^B:/)).toBeInTheDocument();

    // Should NOT render old "保留 A" / "保留 B" buttons
    expect(screen.queryByRole("button", { name: /保留\s*A/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /保留\s*B/ })).toBeNull();

    // Should render "提交选择" button (disabled until selection)
    expect(screen.getByRole("button", { name: /提交选择/ })).toBeDisabled();
  });

  it("allows card selection and edit for 2-candidate conflicts", () => {
    const conflict = makeConflict(2);
    const onResolve = vi.fn();
    render(<ConflictResolver conflict={conflict} onResolve={onResolve} />);

    // Click card A
    const cardA = screen.getByText(/^A:/).closest(".ant-card")!;
    fireEvent.click(cardA);

    // Edit button should appear
    expect(screen.getByRole("button", { name: /编\s*辑/ })).toBeInTheDocument();

    // Submit should be enabled
    expect(screen.getByRole("button", { name: /提交选择/ })).not.toBeDisabled();
  });

  it("renders 确认等价 button for semantic_equivalent conflicts", () => {
    const conflict = makeConflict(2, { conflict_type: "semantic_equivalent" });
    const onResolve = vi.fn();
    render(<ConflictResolver conflict={conflict} onResolve={onResolve} />);

    expect(screen.getByRole("button", { name: /确认等价/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /提交选择/ })).toBeNull();
  });
});
