import { test, expect } from "@playwright/test";
import { login, apiToken } from "./_rbac_helpers";

test.describe("RBAC user e2e", () => {
  test("顶栏布局 + 管理页弹回 + 未授权 ns 查询 403", async ({ page, request }) => {
    await login(page, "e2e-rbac-user-x");

    // 顶栏全屏布局 (无侧边栏 → 无"用户管理"导航)
    await expect(page.getByText("用户管理")).toHaveCount(0);

    // 直接导航 /users → 弹回 /
    await page.goto("/users");
    await page.waitForURL((url) => url.pathname === "/", { timeout: 10000 });

    // 未授权 ns (beta) 查询 → 403
    const tokenU = await apiToken(request, "e2e-rbac-user-x");
    const tokenSuper = await apiToken(request, "admin");
    const allNs = await (await request.get("/api/namespaces", {
      headers: { Authorization: `Bearer ${tokenSuper}` },
    })).json();
    const beta = allNs.find((n: any) => n.slug === "e2e-rbac-ns-beta");
    const resp = await request.post("/api/query/stream", {
      headers: { Authorization: `Bearer ${tokenU}` },
      data: { namespace_id: beta.id, question: "count orders" },
    });
    expect(resp.status()).toBe(403);

    // user 的 namespace 列表只含 alpha (Phase 2 list 过滤)
    const myNs = await (await request.get("/api/namespaces", {
      headers: { Authorization: `Bearer ${tokenU}` },
    })).json();
    const mySlugs = myNs.map((n: any) => n.slug);
    expect(mySlugs).toContain("e2e-rbac-ns-alpha");
    expect(mySlugs).not.toContain("e2e-rbac-ns-beta");
  });
});
