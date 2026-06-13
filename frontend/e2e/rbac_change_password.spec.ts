import { test, expect } from "@playwright/test";
import { login, PWD } from "./_rbac_helpers";

test.describe("RBAC 改密 e2e", () => {
  test("导航可达 + waitForRequest 校验 PUT (L4)", async ({ page }) => {
    // L4 可达性: 从首页点导航入口走到 /profile, 不是直接 goto
    await login(page, "e2e-rbac-user-x", PWD);
    await page.getByRole("button", { name: "修改密码" }).click();
    await page.waitForURL((url) => url.pathname === "/profile", { timeout: 10000 });

    // 填表 + 校验真实发出 PUT /api/auth/password
    await page.getByPlaceholder("当前密码").fill(PWD);
    await page.getByPlaceholder("至少 8 位, 字母+数字").fill("probe12345");
    await page.getByPlaceholder("再次输入新密码").fill("probe12345");
    const [req] = await Promise.all([
      page.waitForRequest((r) => r.url().includes("/api/auth/password") && r.method() === "PUT"),
      page.getByRole("button", { name: /提\s*交/ }).click(),
    ]);
    expect(req.postDataJSON()).toMatchObject({ old_password: PWD, new_password: "probe12345" });
    await expect(page.getByText("密码修改成功")).toBeVisible({ timeout: 10000 });
    // 改回 (幂等)
    await page.getByPlaceholder("当前密码").fill("probe12345");
    await page.getByPlaceholder("至少 8 位, 字母+数字").fill(PWD);
    await page.getByPlaceholder("再次输入新密码").fill(PWD);
    await page.getByRole("button", { name: /提\s*交/ }).click();
    await expect(page.getByText("密码修改成功")).toBeVisible({ timeout: 10000 });
  });

  test("user 改密 → 旧密失败 / 新密成功 → 改回 (幂等)", async ({ page }) => {
    const NEW = "newpass456";
    await login(page, "e2e-rbac-user-x", PWD);

    await page.goto("/profile");
    await page.getByPlaceholder("当前密码").fill(PWD);
    await page.getByPlaceholder("至少 8 位, 字母+数字").fill(NEW);
    await page.getByPlaceholder("再次输入新密码").fill(NEW);
    await page.getByRole("button", { name: /提\s*交/ }).click();
    await expect(page.getByText("密码修改成功")).toBeVisible({ timeout: 10000 });

    // 登出 → 旧密登录失败
    await page.goto("/login");
    await page.getByPlaceholder("username").fill("e2e-rbac-user-x");
    await page.getByPlaceholder("password").fill(PWD);
    await page.getByRole("button", { name: /登\s*录/ }).click();
    await expect(page.getByText(/Incorrect|失败|credentials/i)).toBeVisible({ timeout: 10000 });

    // 新密登录成功
    await login(page, "e2e-rbac-user-x", NEW);

    // 改回原密 (幂等可重跑)
    await page.goto("/profile");
    await page.getByPlaceholder("当前密码").fill(NEW);
    await page.getByPlaceholder("至少 8 位, 字母+数字").fill(PWD);
    await page.getByPlaceholder("再次输入新密码").fill(PWD);
    await page.getByRole("button", { name: /提\s*交/ }).click();
    await expect(page.getByText("密码修改成功")).toBeVisible({ timeout: 10000 });
  });
});
