import { test, expect } from "@playwright/test";

test.describe("Mongo Canonical Knowledge 标签页", () => {
  test.beforeEach(async ({ page }) => {
    // ── Mock 认证 ──
    await page.addInitScript(() => {
      localStorage.setItem("token", "test-token");
      localStorage.setItem(
        "user",
        JSON.stringify({ id: 1, username: "admin", role: "admin", is_active: true })
      );
    });

    // ── Mock API ──
    await page.route("**/api/users/me", (route) =>
      route.fulfill({
        json: { id: 1, username: "admin", role: "admin", is_active: true, created_at: "2024-01-01" },
      })
    );
    await page.route("**/api/namespaces", (route) =>
      route.fulfill({
        json: [{ id: 1, name: "demo namespace", slug: "demo", description: "", created_at: "2024-01-01" }],
      })
    );
    await page.route("**/api/namespaces/1/repos", (route) => route.fulfill({ json: [] }));
    await page.route("**/api/namespaces/1/knowledge", (route) => route.fulfill({ json: [] }));
    await page.route("**/api/namespaces/1/mongo/canonical", (route) =>
      route.fulfill({
        json: [
          {
            id: 1,
            database_name: "crm",
            collection_name: "user",
            identity_key: "crm.user",
            status: "conflicted",
            version: 1,
            last_merged_at: null,
            last_projected_at: null,
          },
        ],
      })
    );
    await page.route("**/api/namespaces/1/mongo/conflicts", (route) =>
      route.fulfill({
        json: [
          {
            id: 1,
            database_name: "crm",
            collection_name: "user",
            conflict_type: "field_type",
            conflict_key: "phone",
            candidate_payload_json: '[{"type":"string"},{"type":"int"}]',
            status: "open",
            resolution_json: "",
          },
        ],
      })
    );
    await page.route("**/api/namespaces/1/mongo/conflicts/1/resolve", (route) =>
      route.fulfill({ json: { status: "resolved" } })
    );
  });

  test("mongo canonical conflict can be resolved from knowledge page", async ({ page }) => {
    await page.goto("/knowledge");
    await page.waitForLoadState("networkidle");

    // ── 选择命名空间 ──
    const nsSelect = page.locator('[class*="namespaceSelect"], select, [role="combobox"]').first();
    if (await nsSelect.isVisible({ timeout: 2000 }).catch(() => false)) {
      await nsSelect.click();
      const demoOption = page.getByText("demo namespace");
      if (await demoOption.isVisible({ timeout: 1000 }).catch(() => false)) {
        await demoOption.click();
      }
    }

    // ── Mongo Canonical 标签页应可见 ──
    const mongoTab = page.getByRole("button", { name: "Mongo Canonical" });
    await expect(mongoTab).toBeVisible({ timeout: 5000 });
    await mongoTab.click();

    // ── 等待内容加载 ──
    await page.waitForLoadState("networkidle");

    // ── 验证 canonical 集合列表 ──
    await expect(page.locator("text=crm.user").first()).toBeVisible({ timeout: 5000 });

    // ── 验证冲突列表 ──
    await expect(page.locator("text=phone").first()).toBeVisible({ timeout: 5000 });

    // ── 点击解决冲突 ──
    const resolveBtn = page.getByRole("button", { name: "解决冲突" }).first();
    await expect(resolveBtn).toBeVisible({ timeout: 3000 });
    await resolveBtn.click();

    // ── 填写解决值 ──
    const input = page.getByPlaceholder("输入确认后的值");
    await expect(input).toBeVisible({ timeout: 3000 });
    await input.fill('{"type":"string"}');

    // ── 提交 ── (Ant Design 在中文字符间插入空格, 用 dialog 作用域 + last() 定位主按钮)
    await page.getByRole("dialog").getByRole("button").last().click();

    // ── 验证成功状态 ──
    await expect(page.getByText("已解决")).toBeVisible({ timeout: 5000 });
  });
});
