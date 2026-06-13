import { test, expect } from "@playwright/test";
import { login, apiToken } from "./_rbac_helpers";

test.describe("RBAC admin e2e", () => {
  test("只见自己 ns + 只见自己创建的 user + 角色仅 user", async ({ page, request }) => {
    await login(page, "e2e-rbac-admin-a");

    // namespace 只见 alpha (经 API 断言)
    const token = await apiToken(request, "e2e-rbac-admin-a");
    const nsResp = await request.get("/api/namespaces", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const slugs = (await nsResp.json()).map((n: any) => n.slug);
    expect(slugs).toContain("e2e-rbac-ns-alpha");
    expect(slugs).not.toContain("e2e-rbac-ns-beta");

    // 用户管理: 只见自己创建的 user-x, 看不到 admin_b
    await page.goto("/users");
    await expect(page.getByText("e2e-rbac-user-x").first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("e2e-rbac-admin-b")).toHaveCount(0);

    // 创建用户弹窗角色只有 user
    await page.getByRole("button", { name: "创建用户" }).click();
    await page.locator(".ant-modal .ant-select").click();
    await expect(page.getByText("超级管理员 (super_admin)")).toHaveCount(0);
  });

  test("admin 越权访问他人 ns 的 API 返 403", async ({ request }) => {
    const tokenA = await apiToken(request, "e2e-rbac-admin-a");
    const tokenSuper = await apiToken(request, "admin");
    const allNs = await (await request.get("/api/namespaces", {
      headers: { Authorization: `Bearer ${tokenSuper}` },
    })).json();
    const beta = allNs.find((n: any) => n.slug === "e2e-rbac-ns-beta");
    // 越权 P 类: admin_a 访问 beta 的 knowledge → 403
    const resp = await request.get(`/api/namespaces/${beta.id}/knowledge`, {
      headers: { Authorization: `Bearer ${tokenA}` },
    });
    expect(resp.status()).toBe(403);
    // 越权 Q 类: agent-traces 传 beta → 403
    const tr = await request.get(`/api/agent-traces?namespace_id=${beta.id}`, {
      headers: { Authorization: `Bearer ${tokenA}` },
    });
    expect(tr.status()).toBe(403);
  });
});
