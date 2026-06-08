/**
 * E2E: Enum binding flow — real login, no API mocking.
 *
 * Prerequisites: backend running on localhost:8001, frontend on localhost:3000
 * Admin credentials: admin / Cb1392010
 */
import { test, expect } from '@playwright/test';

test.describe('enum binding flow', () => {
  test.beforeEach(async ({ page }) => {
    // Real login
    await page.goto('/login');
    await page.getByPlaceholder('username').fill('admin');
    await page.getByPlaceholder('password').fill('Cb1392010');
    await page.getByRole('button', { name: /登 录/ }).click();

    // Wait for redirect to home page after login
    await page.waitForURL('/', { timeout: 10000 });
  });

  test('navigate to schema tab and see enum dictionary sub-tab', async ({ page }) => {
    await page.goto('/knowledge');

    // Click "Schema 管理" tab
    const schemaTab = page.getByText('Schema 管理');
    await expect(schemaTab).toBeVisible({ timeout: 10000 });
    await schemaTab.click();

    // "枚举字典" sub-tab should be visible inside SchemaCanonicalPanel
    const enumTab = page.getByText('枚举字典');
    await expect(enumTab).toBeVisible({ timeout: 10000 });
  });

  test('click enum dictionary tab and see table', async ({ page }) => {
    await page.goto('/knowledge');

    // Navigate to Schema 管理 tab
    const schemaTab = page.getByText('Schema 管理');
    await expect(schemaTab).toBeVisible({ timeout: 10000 });
    await schemaTab.click();

    // Click 枚举字典 sub-tab
    const enumTab = page.getByText('枚举字典');
    await expect(enumTab).toBeVisible({ timeout: 10000 });
    await enumTab.click();

    // Should see the "新建枚举" button (table may be empty but UI renders)
    await expect(page.getByText('新建枚举')).toBeVisible({ timeout: 5000 });
  });

  test('reachability: pending-enum-binding tab triggers real network call', async ({ page }) => {
    // 验收 Layer 4 — 从导航起点走到新 tab, 断言网络请求 URL 精确匹配,
    // 防"组件存在但没挂"或"挂了但 path 错"两类挂载层 bug.
    await page.goto('/knowledge');

    const schemaTab = page.getByText('Schema 管理');
    await expect(schemaTab).toBeVisible({ timeout: 10000 });
    await schemaTab.click();

    // "待绑定枚举" tab 必须在 Schema panel 内可见 — 之前组件没挂 import 链, 不可见
    const pendingTab = page.getByText('待绑定枚举');
    await expect(pendingTab).toBeVisible({ timeout: 10000 });

    // 点击 tab 必须触发对 pending_enum_binding 端点的真实 GET, URL 精确含 nsId 段
    const reqPromise = page.waitForRequest(
      (req) =>
        /\/api\/namespaces\/\d+\/schema-canonical\/fields\/pending_enum_binding/.test(
          req.url(),
        ) && req.method() === 'GET',
      { timeout: 8000 },
    );
    await pendingTab.click();
    const req = await reqPromise;
    // 双重断言: query string 不包含 namespace_id (旧 bug, 后端不接受)
    expect(req.url()).not.toMatch(/namespace_id=/);
  });

  test('knowledge page tabs are visible after login', async ({ page }) => {
    await page.goto('/knowledge');

    // Core tabs should be visible
    await expect(page.getByText('知识条目')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Schema 管理')).toBeVisible({ timeout: 10000 });
  });
});
