import { describe, it, expect, vi, beforeEach } from "vitest";

// L1 契约: mock 共享 axios 实例 (NOT @/api 模块) → 断言真实 api/index.ts 产出的
// URL + method + body。组件经真实 @/api import 路径, axios 实例被 mock 截获。

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();

vi.mock("axios", () => ({
  default: {
    create: () => ({
      get: (...args: any[]) => mockGet(...args),
      post: (...args: any[]) => mockPost(...args),
      patch: (...args: any[]) => mockPatch(...args),
      delete: (...args: any[]) => mockDelete(...args),
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
    }),
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockResolvedValue({
    data: [
      {
        id: 1, name: "java-spring", display_name: "Java Spring",
        description: "", languages: ["Java"], hint_text: "",
        is_builtin: true, is_enabled: true,
        created_at: "2026-01-01", updated_at: "2026-01-01",
      },
    ],
  });
  mockPost.mockResolvedValue({ data: { id: 2, name: "e2e-test" } });
  mockPatch.mockResolvedValue({ data: { id: 5, profile_id: 2 } });
  mockDelete.mockResolvedValue({ data: { ok: true } });
});

describe("Profile API L1 — axios-instance URL + method + body contract", () => {
  it("fetchProfiles calls GET /profiles", async () => {
    const { fetchProfiles } = await import("../api");
    await fetchProfiles();
    expect(mockGet).toHaveBeenCalledWith("/profiles");
  });

  it("createProfile POSTs correct body to /profiles", async () => {
    const { createProfile } = await import("../api");
    await createProfile({
      name: "e2e-test", display_name: "E2E Test",
      languages: ["Java"], hint_text: "find @DataObject",
    });
    expect(mockPost).toHaveBeenCalledWith(
      "/profiles",
      expect.objectContaining({
        name: "e2e-test",
        display_name: "E2E Test",
        hint_text: "find @DataObject",
      }),
    );
  });

  it("updateProfile PATCHes /profiles/{id}", async () => {
    const { updateProfile } = await import("../api");
    await updateProfile(7, { hint_text: "x" });
    expect(mockPatch).toHaveBeenCalledWith("/profiles/7", { hint_text: "x" });
  });

  it("deleteProfile DELETEs /profiles/{id}", async () => {
    const { deleteProfile } = await import("../api");
    await deleteProfile(9);
    expect(mockDelete).toHaveBeenCalledWith("/profiles/9");
  });

  it("updateRepoProfile PATCHes repo with profile_id body", async () => {
    const { updateRepoProfile } = await import("../api");
    await updateRepoProfile(3, 5, 2);
    expect(mockPatch).toHaveBeenCalledWith("/namespaces/3/repos/5", { profile_id: 2 });
  });

  it("updateRepoProfile sends null for clear", async () => {
    const { updateRepoProfile } = await import("../api");
    await updateRepoProfile(3, 5, null);
    expect(mockPatch).toHaveBeenCalledWith("/namespaces/3/repos/5", { profile_id: null });
  });
});

describe("ProfileManagement component — mount triggers GET /profiles", () => {
  it("renders and fetches profiles on mount", async () => {
    const { render, screen, waitFor } = await import("@testing-library/react");
    const React = await import("react");
    const { default: ProfileManagement } = await import("../pages/ProfileManagement");
    render(React.createElement(ProfileManagement));
    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith("/profiles");
    });
    await waitFor(() => {
      expect(screen.getByText("java-spring")).toBeTruthy();
    });
  });
});
