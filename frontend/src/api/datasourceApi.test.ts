import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import axios from "axios";
import { addDataSource, fetchDataSources } from "@/api";

vi.mock("axios", () => {
  const mockAxios = {
    create: vi.fn(() => mockAxios),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(),
  };
  return { default: mockAxios };
});

const mockAxios = axios as unknown as { post: Mock; get: Mock };

describe("数据源 API 契约", () => {
  beforeEach(() => vi.clearAllMocks());

  it("addDataSource POST body 含 description", async () => {
    mockAxios.post.mockResolvedValueOnce({
      data: { id: 1, db_type: "mysql", database: "d", description: "订单库", db_profile: {} },
    });
    await addDataSource(3, {
      db_type: "mysql", host: "h", port: 3306, database: "d",
      username: "u", password: "p", description: "订单库",
    });
    expect(mockAxios.post).toHaveBeenCalledWith(
      "/namespaces/3/datasources",
      expect.objectContaining({ description: "订单库" }),
    );
  });

  it("fetchDataSources 解析响应含 description + db_profile", async () => {
    mockAxios.get.mockResolvedValueOnce({
      data: [{ id: 1, db_type: "mysql", database: "d", host: "h", port: 3306,
               username: "u", description: "订单库",
               db_profile: { version: "8.0", object_count: 12 }, created_at: "2026-06-14" }],
    });
    const out = await fetchDataSources(3);
    expect(out[0].description).toBe("订单库");
    expect(out[0].db_profile.object_count).toBe(12);
  });

  it("addDataSource Oracle db_type POST body 序列化正确", async () => {
    mockAxios.post.mockResolvedValueOnce({
      data: {
        id: 5, db_type: "oracle", database: "orclpdb",
        description: "Oracle 生产库", db_profile: { version: "Oracle Database 19c", object_count: 50 },
        host: "db.example.com", port: 1521, username: "hr", created_at: "2026-06-15",
      },
    });
    await addDataSource(3, {
      db_type: "oracle", host: "db.example.com", port: 1521,
      database: "orclpdb", username: "hr", password: "secret", description: "Oracle 生产库",
    });
    expect(mockAxios.post).toHaveBeenCalledWith(
      "/namespaces/3/datasources",
      expect.objectContaining({ db_type: "oracle", port: 1521, database: "orclpdb" }),
    );
  });

  it("fetchDataSources 解析 Oracle 响应 db_type 为 oracle", async () => {
    mockAxios.get.mockResolvedValueOnce({
      data: [{
        id: 5, db_type: "oracle", database: "orclpdb", host: "db.example.com",
        port: 1521, username: "hr", description: "Oracle 生产库",
        db_profile: { version: "Oracle Database 19c", schema: "HR", object_count: 50 },
        created_at: "2026-06-15",
      }],
    });
    const out = await fetchDataSources(3);
    expect(out[0].db_type).toBe("oracle");
    expect(out[0].db_profile.schema).toBe("HR");
  });
});
