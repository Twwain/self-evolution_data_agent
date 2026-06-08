/**
 * Bug condition exploration test — ConflictsTab trailing dot for empty field_path.
 *
 * **Validates: Requirements 1.5, 1.6**
 *
 * This test is EXPECTED TO FAIL on unfixed code — failure confirms the bug exists.
 * DO NOT fix the code or tests when they fail.
 *
 * Bug 3 (UI title): ConflictsTab renders `{target}.{field_path}` which produces
 * a trailing dot when field_path is empty string.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import * as fc from "fast-check";

// Generate arbitrary target names (valid SQL table identifiers)
const targetArb = fc
  .stringMatching(/^[a-z_][a-z0-9_]{2,19}$/)
  .filter((s) => s.length >= 3 && s.length <= 20);

describe("Bug 3: ConflictsTab empty field_path trailing dot", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("title does NOT end with '.' when field_path is empty", async () => {
    await fc.assert(
      fc.asyncProperty(targetArb, async (target) => {
        vi.resetModules();

        // Mock the API to return a conflict with empty field_path
        vi.doMock("@/api", () => ({
          schemaCanonicalApi: {
            listConflicts: vi.fn().mockResolvedValue([
              {
                id: 1,
                target,
                field_path: "",
                database: "test_db",
                db_type: "mysql",
                candidate_kind: "table_description",
                conflict_type: "field_value",
                candidates_snapshot: [
                  { candidate_id: 1, value: { description: "desc A" }, evidence: [], confidence_status: "confirmed_by_code" },
                  { candidate_id: 2, value: { description: "desc B" }, evidence: [], confidence_status: "confirmed_by_introspect" },
                ],
                status: "open" as const,
                resolution_choice: null,
                resolved_at: null,
                created_at: "2026-01-01",
              },
            ]),
            resolveConflict: vi.fn().mockResolvedValue({}),
          },
        }));

        // Re-import to pick up the new mock
        const { ConflictsTab } = await import("./ConflictsTab");

        const { container, unmount } = render(<ConflictsTab namespaceId={1} />);

        // Wait for the conflict to render
        await waitFor(() => {
          const strong = container.querySelector("strong");
          expect(strong).not.toBeNull();
        });

        const strong = container.querySelector("strong");
        const titleText = strong!.textContent || "";

        // The title should NOT end with a dot when field_path is empty
        expect(titleText.endsWith(".")).toBe(false);
        // The title should just be the target name
        expect(titleText).toBe(target);

        unmount();
        vi.doUnmock("@/api");
      }),
      { numRuns: 5 },
    );
  });
});
