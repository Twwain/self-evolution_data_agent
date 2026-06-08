import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import axios from "axios";
import { enumApi } from "@/api";

// Mock axios at module level
vi.mock("axios", () => {
  const mockAxios = {
    create: vi.fn(() => mockAxios),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  };
  return { default: mockAxios };
});

const mockAxios = axios as unknown as {
  create: Mock;
  get: Mock;
  post: Mock;
  put: Mock;
  delete: Mock;
};

describe("enumApi", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("createEnumDictionary posts to /enum-dictionary", async () => {
    mockAxios.post.mockResolvedValueOnce({ data: { id: 1, source: "manual" } });

    const res = await enumApi.createEnumDictionary({
      namespace_id: 1,
      db_type: "mongodb",
      enum_class_name: "OrderStatus",
      values: [{ name: "PAID", db_value: 1 }],
    });

    expect(res).toEqual({ id: 1, source: "manual" });
    expect(mockAxios.post).toHaveBeenCalledWith("/enum-dictionary", {
      namespace_id: 1,
      db_type: "mongodb",
      enum_class_name: "OrderStatus",
      values: [{ name: "PAID", db_value: 1 }],
    });
  });

  it("updateEnumCanonical puts to /enum-dictionary/{id}", async () => {
    mockAxios.put.mockResolvedValueOnce({ data: { id: 5, source: "manual" } });

    await enumApi.updateEnumCanonical(5, { values: [{ name: "A", db_value: 0 }] });

    expect(mockAxios.put).toHaveBeenCalledWith("/enum-dictionary/5", {
      values: [{ name: "A", db_value: 0 }],
    });
  });

  it("deleteEnumCanonical sends DELETE with dry_run param", async () => {
    mockAxios.delete.mockResolvedValueOnce({ data: { affected_fields: 3 } });

    await enumApi.deleteEnumCanonical(10, { dryRun: true });

    expect(mockAxios.delete).toHaveBeenCalledWith("/enum-dictionary/10", {
      params: { dry_run: true },
    });
  });

  it("bindFieldEnum posts to namespaces/{nsId}/schema-canonical bind_enum endpoint", async () => {
    mockAxios.post.mockResolvedValueOnce({
      data: { field: "status", enum_match_status: "matched" },
    });

    const res = await enumApi.bindFieldEnum(7, 42, "status", { enum_dict_id: 99 });

    expect(res.enum_match_status).toBe("matched");
    expect(mockAxios.post).toHaveBeenCalledWith(
      "/namespaces/7/schema-canonical/42/fields/status/bind_enum",
      { enum_dict_id: 99 },
    );
  });

  it("unbindFieldEnum sends DELETE to namespaces/{nsId}/schema-canonical bind_enum endpoint", async () => {
    mockAxios.delete.mockResolvedValueOnce({
      data: { field: "status", enum_match_status: "pending" },
    });

    const res = await enumApi.unbindFieldEnum(7, 42, "status");

    expect(res.enum_match_status).toBe("pending");
    expect(mockAxios.delete).toHaveBeenCalledWith(
      "/namespaces/7/schema-canonical/42/fields/status/bind_enum",
    );
  });

  it("listPendingEnumBindings GETs scoped under namespaces/{nsId}/schema-canonical", async () => {
    mockAxios.get.mockResolvedValueOnce({
      data: { items: [], total: 0 },
    });

    const res = await enumApi.listPendingEnumBindings(1);

    expect(res).toEqual({ items: [], total: 0 });
    expect(mockAxios.get).toHaveBeenCalledWith(
      "/namespaces/1/schema-canonical/fields/pending_enum_binding",
      { params: { page: 1, size: 50 } },
    );
  });

  it("listEnumDictionaries GETs with filters", async () => {
    mockAxios.get.mockResolvedValueOnce({
      data: { items: [{ id: 1, enum_class_name: "X" }], total: 1 },
    });

    await enumApi.listEnumDictionaries({ namespace_id: 1, source: "code" });

    expect(mockAxios.get).toHaveBeenCalledWith("/enum-dictionary", {
      params: { namespace_id: 1, source: "code" },
    });
  });
});
