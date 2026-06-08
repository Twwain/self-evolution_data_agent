/**
 * Preservation property tests — ConflictResolver baseline behavior.
 *
 * **Validates: Requirements 3.3, 3.4**
 *
 * These tests confirm correct behavior:
 * - 2-candidate conflicts render selectable cards with "提交选择" button (Req 3.3)
 * - semantic_equivalent conflicts render "确认等价" button (Req 3.4)
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import * as fc from "fast-check";
import { ConflictResolver } from "@/components/schema/ConflictResolver";
import type { SchemaConflict } from "@/types/schema-canonical";

// ════════════════════════════════════════════════════════════════
//  Arbitraries
// ════════════════════════════════════════════════════════════════

/** Generate a candidate snapshot entry */
const candidateArb = fc.record({
  candidate_id: fc.nat({ max: 10000 }),
  value: fc.record({
    description: fc.string({ minLength: 1, maxLength: 50 }),
  }),
  evidence: fc.constant([] as unknown[]),
  confidence_status: fc.constantFrom(
    "confirmed_by_code",
    "confirmed_by_introspect",
    "evidence_only",
  ),
});

/** Generate a 2-candidate conflict (standard A vs B scenario) */
const twoCandidateConflictArb = fc.record({
  id: fc.nat({ max: 10000 }),
  db_type: fc.constantFrom("mysql", "mongodb"),
  database: fc.string({ minLength: 1, maxLength: 20 }),
  target: fc.string({ minLength: 1, maxLength: 30 }),
  field_path: fc.string({ minLength: 0, maxLength: 30 }),
  candidate_kind: fc.constantFrom("field_description", "table_description", "enum_values"),
  conflict_type: fc.constantFrom("value_conflict", "multi_source"),
  candidates_snapshot: fc.tuple(candidateArb, candidateArb).map(([a, b]) => [a, b]),
  status: fc.constant("open" as const),
  resolution_choice: fc.constant(null),
  resolved_at: fc.constant(null),
  created_at: fc.constant("2026-01-01T00:00:00Z"),
});

/** Generate a semantic_equivalent conflict (any number of candidates) */
const semanticEquivalentConflictArb = fc.record({
  id: fc.nat({ max: 10000 }),
  db_type: fc.constantFrom("mysql", "mongodb"),
  database: fc.string({ minLength: 1, maxLength: 20 }),
  target: fc.string({ minLength: 1, maxLength: 30 }),
  field_path: fc.string({ minLength: 0, maxLength: 30 }),
  candidate_kind: fc.constantFrom("field_description", "table_description"),
  conflict_type: fc.constant("semantic_equivalent"),
  candidates_snapshot: fc.array(candidateArb, { minLength: 2, maxLength: 5 }),
  status: fc.constant("open" as const),
  resolution_choice: fc.constant(null),
  resolved_at: fc.constant(null),
  created_at: fc.constant("2026-01-01T00:00:00Z"),
});

// ════════════════════════════════════════════════════════════════
//  Property 4: 2-candidate conflicts → card selection UI
//  **Validates: Requirements 3.3**
// ════════════════════════════════════════════════════════════════

describe("ConflictResolver Preservation Properties", () => {
  it("For all conflicts with candidates_snapshot.length === 2: UI renders card selection with submit button", () => {
    fc.assert(
      fc.property(twoCandidateConflictArb, (conflict) => {
        const onResolve = vi.fn();
        const { unmount } = render(
          <ConflictResolver conflict={conflict as SchemaConflict} onResolve={onResolve} />,
        );

        // Should render cards with A/B labels
        expect(screen.getByText(/^A:/)).toBeInTheDocument();
        expect(screen.getByText(/^B:/)).toBeInTheDocument();

        // Should render "提交选择" button (disabled until selection)
        const submitBtn = screen.getByRole("button", { name: /提交选择/ });
        expect(submitBtn).toBeInTheDocument();
        expect(submitBtn).toBeDisabled();

        // Should NOT render old "保留 A" / "保留 B" buttons
        expect(screen.queryByRole("button", { name: /保留\s*A/ })).toBeNull();
        expect(screen.queryByRole("button", { name: /保留\s*B/ })).toBeNull();

        unmount();
      }),
      { numRuns: 20 },
    );
  });

  // ════════════════════════════════════════════════════════════════
  //  Property 5: semantic_equivalent → "确认等价" button
  //  **Validates: Requirements 3.4**
  // ════════════════════════════════════════════════════════════════

  it("For all conflicts with conflict_type === 'semantic_equivalent': UI renders '确认等价' button", () => {
    fc.assert(
      fc.property(semanticEquivalentConflictArb, (conflict) => {
        const onResolve = vi.fn();
        const { unmount } = render(
          <ConflictResolver conflict={conflict as SchemaConflict} onResolve={onResolve} />,
        );

        const confirmBtn = screen.getByRole("button", { name: /确认等价/ });
        expect(confirmBtn).toBeInTheDocument();

        // Should NOT render card selection UI for semantic_equivalent
        expect(screen.queryByRole("button", { name: /提交选择/ })).toBeNull();

        unmount();
      }),
      { numRuns: 20 },
    );
  });
});
