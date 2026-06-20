import { test, expect } from "@playwright/test";
import { login } from "./_rbac_helpers";

// L4 可达性: 从 app 入口 → login → 侧边栏 "Profile 管理" (证明导航入口已挂载)
test("profile management reachable from app entry", async ({ page }) => {
  await login(page, "admin");
  await page.getByText("Profile 管理").click();
  await page.waitForURL((u) => u.pathname.includes("/profiles"), { timeout: 10000 });
  await page.waitForResponse((r) => r.url().includes("/api/profiles") && r.status() === 200);
  await expect(page.getByRole("heading", { name: "Profile 管理" })).toBeVisible();
});

test.describe("Profile Management CRUD", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "admin");
    await page.goto("/profiles");
    await page.waitForSelector("h2");
  });

  test("renders builtin + custom tabs with seed data", async ({ page }) => {
    await expect(page.getByText(/内置模板/)).toBeVisible();
    await expect(page.getByText(/自定义/)).toBeVisible();
    await expect(page.getByText("java-spring")).toBeVisible();
  });

  test("create flow — form renders, submit, new profile in list", async ({ page }) => {
    await page.getByRole("button", { name: "新建 Profile" }).click();
    await expect(page.getByLabel("名称 (slug)")).toBeVisible();
    await page.getByLabel("名称 (slug)").fill("e2e-test-profile");
    await page.getByLabel("显示名").fill("E2E Test Profile");
    await page.getByLabel("提示文本 (Hint)").fill("find @DataObject annotated classes");
    await page.getByRole("button", { name: /创\s*建/ }).click();
    await page.getByText("自定义").click();
    await expect(page.getByText("e2e-test-profile")).toBeVisible({ timeout: 5000 });
  });

  test("builtin profile cannot be deleted — delete button absent in builtin tab", async ({ page }) => {
    // 内置 tab 默认激活, java-spring 行不应有删除按钮
    await expect(page.getByText("java-spring")).toBeVisible();
    const builtinRow = page.getByRole("row", { name: /java-spring/ });
    await expect(builtinRow.getByRole("button", { name: /删\s*除/ })).toHaveCount(0);
  });
});
