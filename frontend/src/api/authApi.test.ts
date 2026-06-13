import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import axios from "axios";
import { changePassword, resetUserPassword, setUserAccess } from "@/api";

vi.mock("axios", () => {
  const mockAxios = {
    create: vi.fn(() => mockAxios),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(),
  };
  return { default: mockAxios };
});

const mockAxios = axios as unknown as { post: Mock; put: Mock };

describe("auth API 契约", () => {
  beforeEach(() => vi.clearAllMocks());

  it("changePassword PUT /auth/password 带 old+new", async () => {
    mockAxios.put.mockResolvedValueOnce({ data: { status: "ok" } });
    await changePassword({ old_password: "old12345", new_password: "new12345" });
    expect(mockAxios.put).toHaveBeenCalledWith("/auth/password", {
      old_password: "old12345", new_password: "new12345",
    });
  });

  it("resetUserPassword POST /users/{id}/reset-password 带 new_password", async () => {
    mockAxios.post.mockResolvedValueOnce({ data: { status: "ok" } });
    await resetUserPassword(7, "new12345");
    expect(mockAxios.post).toHaveBeenCalledWith("/users/7/reset-password", {
      new_password: "new12345",
    });
  });

  it("setUserAccess PUT /users/{id}/access 带 { namespace_ids: [...] }", async () => {
    mockAxios.put.mockResolvedValueOnce({ data: { status: "ok", namespace_ids: [1, 3] } });
    await setUserAccess(5, [1, 3]);
    expect(mockAxios.put).toHaveBeenCalledWith("/users/5/access", {
      namespace_ids: [1, 3],
    });
  });
});
