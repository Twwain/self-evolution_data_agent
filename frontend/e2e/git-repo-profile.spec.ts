import { test, expect } from "@playwright/test";
import { login } from "./_rbac_helpers";

// RepoManager 在 NamespacePage 的 "Git 仓库" Tab 中。
// L4 reachability: app 入口 → login → /namespaces (NamespaceSelector 自动选中 ns)
// → Git 仓库 tab → RepoManager add-form 的 Profile 选择器。
test.describe("Git Repo Profile Selector", () => {
  test("repo tab exposes profile selector (add-form)", async ({ page }) => {
    await login(page, "admin");
    await page.goto("/namespaces");

    // NamespaceSelector 自动恢复/选中 ns → Tabs 出现
    const repoTab = page.getByRole("tab", { name: /Git\s*仓库|仓库/ });
    await expect(repoTab).toBeVisible({ timeout: 10000 });
    await repoTab.click();

    // RepoManager add-form: Profile 字段 label + allowClear Select (placeholder 含"自动识别")
    await expect(page.getByText("Profile").first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText("不选 (自动识别)").first()).toBeVisible({ timeout: 5000 });
  });
});
