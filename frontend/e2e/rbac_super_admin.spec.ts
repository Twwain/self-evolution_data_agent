import { test, expect } from "@playwright/test";
import { login, apiToken } from "./_rbac_helpers";

test.describe("RBAC super_admin e2e", () => {
  test("全局可见 + 三级角色创建", async ({ page, request }) => {
    await login(page, "admin");

    // 侧边栏布局 (super_admin 不被锁成 user 顶栏)
    await expect(page.getByText("用户管理")).toBeVisible();

    // 用户管理见全部账号
    await page.goto("/users");
    await expect(page.getByText("e2e-rbac-admin-a")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("e2e-rbac-admin-b")).toBeVisible();

    // namespace 见全部
    const token = await apiToken(request, "admin");
    const nsResp = await request.get("/api/namespaces", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const slugs = (await nsResp.json()).map((n: any) => n.slug);
    expect(slugs).toContain("e2e-rbac-ns-alpha");
    expect(slugs).toContain("e2e-rbac-ns-beta");

    // 创建用户弹窗角色三选可见 (仅取可见 dropdown 的 option, 避开详情表单隐藏 dropdown)
    await page.getByRole("button", { name: "创建用户" }).click();
    await page.locator(".ant-modal .ant-select").click();
    const dropdown = page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)");
    await expect(dropdown.getByText("超级管理员 (super_admin)")).toBeVisible();
    await expect(dropdown.getByText("管理员 (admin)", { exact: true })).toBeVisible();
  });

  // ── L4 可达性: 重置密码按钮 (POST /api/users/{id}/reset-password) ──
  test("重置密码按钮可达 + waitForRequest 校验 POST (L4)", async ({ page }) => {
    await login(page, "admin");
    await page.getByText("用户管理").click();
    await page.waitForURL((url) => url.pathname === "/users", { timeout: 10000 });
    await page.getByText("e2e-rbac-user-x").click();
    await page.getByRole("button", { name: "重置密码" }).click();
    await page.locator(".ant-modal input[type='password']").fill("reset12345");
    const [req] = await Promise.all([
      page.waitForRequest(
        (r) => /\/api\/users\/\d+\/reset-password$/.test(r.url()) && r.method() === "POST",
      ),
      page.locator(".ant-modal-footer").getByRole("button", { name: /重\s*置/ }).click(),
    ]);
    expect(req.postDataJSON()).toMatchObject({ new_password: "reset12345" });
    await expect(page.getByText("密码已重置")).toBeVisible({ timeout: 10000 });
    // 改回原密码 (幂等可重跑)
    await page.getByRole("button", { name: "重置密码" }).click();
    await page.locator(".ant-modal input[type='password']").fill("admin123456");
    await page.locator(".ant-modal-footer").getByRole("button", { name: /重\s*置/ }).click();
    await expect(page.getByText("密码已重置")).toBeVisible({ timeout: 10000 });
  });
});
