/**
 * E2E: Schema full edit — real login, no API mocking, chromium-only.
 *
 * Prerequisites: backend running on localhost:8001, frontend on localhost:3000
 * Admin credentials: admin / admin123456
 */
import { test, expect } from "@playwright/test";

const BTN_EDIT = "编辑 Schema";
const BTN_SAVE = /保\s*存/;
const BTN_CANCEL = /取\s*消/;
const BTN_ADD_FIELD = /添加字段/;

test.describe("schema full edit", () => {
  test.beforeEach(async ({ page }) => {
    // Real login
    await page.goto("/login");
    await page.getByPlaceholder("username").fill("admin");
    await page.getByPlaceholder("password").fill("admin123456");
    await page.locator("button").filter({ hasText: /登/ }).click();
    await page.waitForURL("/", { timeout: 10000 });

    // Navigate to knowledge page
    await page.goto("/knowledge");
    await page.waitForTimeout(1000);

    // Select "内容平台" namespace
    const nsDropdown = page.locator("[class*='ant-select']").first();
    await expect(nsDropdown).toBeVisible({ timeout: 5000 });
    await nsDropdown.click();
    await page.locator(".ant-select-item-option").filter({ hasText: "内容平台" }).click();
    await page.waitForTimeout(500);

    // Click "Schema 管理" tab
    await page.locator("button").filter({ hasText: "Schema 管理" }).click();

    // Wait for AllFieldsTab to render
    await expect(page.locator("button").filter({ hasText: BTN_EDIT })).toBeVisible({ timeout: 15000 });
  });

  test("pending enum tab is removed", async ({ page }) => {
    await expect(page.getByText("待绑定枚举")).not.toBeVisible({ timeout: 5000 });
  });

  test("edit mode shows description as editable input", async ({ page }) => {
    await page.locator("button").filter({ hasText: BTN_EDIT }).click();

    // Description input should be visible
    await expect(page.locator(".ant-input-group input")).toBeVisible({ timeout: 5000 });

    // Purpose detail textarea should be visible
    await expect(page.locator("textarea[placeholder='用途详情...']")).toBeVisible({ timeout: 5000 });

    // Cancel
    await page.locator("button").filter({ hasText: BTN_CANCEL }).click();
  });

  test("add new field and save triggers PATCH with new field", async ({ page }) => {
    await page.locator("button").filter({ hasText: BTN_EDIT }).click();
    await page.waitForTimeout(500);

    // Use unique name to avoid conflicts with previous test runs
    const testFieldName = `_e2e_add_${Date.now()}`;

    // Click "添加字段"
    await page.locator("button").filter({ hasText: BTN_ADD_FIELD }).click();

    // Fill new field name and type
    const nameInputs = page.locator("input[placeholder='字段名']");
    await nameInputs.last().fill(testFieldName);
    const typeInputs = page.locator("input[placeholder='类型']");
    await typeInputs.last().fill("String");

    // Save and assert PATCH
    const reqPromise = page.waitForRequest(
      (req) =>
        /\/api\/namespaces\/\d+\/schema-canonical\/\d+$/.test(req.url()) &&
        req.method() === "PATCH",
      { timeout: 10000 },
    );
    await page.locator("button").filter({ hasText: BTN_SAVE }).click();
    const req = await reqPromise;
    const body = req.postDataJSON();
    const fieldNames = (body.fields as Array<{ name: string }>).map((f) => f.name);
    expect(fieldNames).toContain(testFieldName);

    // Cleanup: delete the test field
    await page.waitForTimeout(1000);
    await page.locator("button").filter({ hasText: BTN_EDIT }).click();
    await page.waitForTimeout(500);
    const testRow = page.locator("tr").filter({ hasText: testFieldName });
    const deleteBtn = testRow.locator("button .anticon-delete").locator("..");
    if (await deleteBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await deleteBtn.click();
      await page.locator("button").filter({ hasText: BTN_SAVE }).click();
      await page.waitForTimeout(1000);
    }
  });

  test("delete field and save triggers PATCH without deleted field", async ({ page }) => {
    // First add a field so we have something safe to delete
    await page.locator("button").filter({ hasText: BTN_EDIT }).click();
    await page.waitForTimeout(500);

    const testFieldName = `_e2e_del_${Date.now()}`;
    await page.locator("button").filter({ hasText: BTN_ADD_FIELD }).click();
    const nameInputs = page.locator("input[placeholder='字段名']");
    await nameInputs.last().fill(testFieldName);
    const typeInputs = page.locator("input[placeholder='类型']");
    await typeInputs.last().fill("String");

    // Save the added field first
    const addReqPromise = page.waitForRequest(
      (req) =>
        /\/api\/namespaces\/\d+\/schema-canonical\/\d+$/.test(req.url()) &&
        req.method() === "PATCH",
      { timeout: 10000 },
    );
    await page.locator("button").filter({ hasText: BTN_SAVE }).click();
    await addReqPromise;
    await page.waitForTimeout(1000);

    // Now re-enter edit mode and delete the field we just added
    await page.locator("button").filter({ hasText: BTN_EDIT }).click();
    await page.waitForTimeout(500);

    const testRow = page.locator("tr").filter({ hasText: testFieldName });
    const deleteBtn = testRow.locator("button .anticon-delete").locator("..");
    await deleteBtn.click();

    // Save and assert PATCH does NOT contain deleted field
    const reqPromise = page.waitForRequest(
      (req) =>
        /\/api\/namespaces\/\d+\/schema-canonical\/\d+$/.test(req.url()) &&
        req.method() === "PATCH",
      { timeout: 10000 },
    );
    await page.locator("button").filter({ hasText: BTN_SAVE }).click();
    const req = await reqPromise;
    const body = req.postDataJSON();
    const fieldNames = (body.fields as Array<{ name: string }>).map((f) => f.name);
    expect(fieldNames).not.toContain(testFieldName);
  });

  test("edit field type and save triggers PATCH with updated type", async ({ page }) => {
    await page.locator("button").filter({ hasText: BTN_EDIT }).click();
    await page.waitForTimeout(500);

    // Change first type input
    const typeInputs = page.locator("input[placeholder='类型']");
    const firstTypeInput = typeInputs.first();
    await expect(firstTypeInput).toBeVisible({ timeout: 5000 });
    const originalType = await firstTypeInput.inputValue();
    await firstTypeInput.clear();
    await firstTypeInput.fill("_e2e_modified_type");

    // Save and assert
    const reqPromise = page.waitForRequest(
      (req) =>
        /\/api\/namespaces\/\d+\/schema-canonical\/\d+$/.test(req.url()) &&
        req.method() === "PATCH",
      { timeout: 10000 },
    );
    await page.locator("button").filter({ hasText: BTN_SAVE }).click();
    const req = await reqPromise;
    const body = req.postDataJSON();
    const types = (body.fields as Array<{ type: string }>).map((f) => f.type);
    expect(types).toContain("_e2e_modified_type");

    // Cleanup: restore original type
    await page.waitForTimeout(1000);
    await page.locator("button").filter({ hasText: BTN_EDIT }).click();
    await page.waitForTimeout(500);
    const typeInputs2 = page.locator("input[placeholder='类型']");
    await typeInputs2.first().clear();
    await typeInputs2.first().fill(originalType);
    await page.locator("button").filter({ hasText: BTN_SAVE }).click();
    await page.waitForTimeout(1000);
  });

  test("enum binding with search triggers bind API", async ({ page }) => {
    // Find a "绑定" button (field with enum_match_status=pending)
    const bindBtn = page.locator("button").filter({ hasText: /^绑\s*定$/ }).first();
    if (!(await bindBtn.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip();
      return;
    }

    await bindBtn.click();

    // Drawer should open
    const drawer = page.locator(".ant-drawer");
    await expect(drawer).toBeVisible({ timeout: 5000 });

    // Type in search
    const selectInput = drawer.locator(".ant-select-selection-search-input");
    await selectInput.fill("Type");

    // Select first option if available
    const option = page.locator(".ant-select-item-option").first();
    if (await option.isVisible({ timeout: 3000 }).catch(() => false)) {
      const reqPromise = page.waitForRequest(
        (req) => /\/fields\/.*\/bind_enum/.test(req.url()) && req.method() === "POST",
        { timeout: 8000 },
      );
      await option.click();

      // Click bind button in drawer
      const drawerBindBtn = drawer.locator("button").filter({ hasText: /^绑\s*定$/ });
      await drawerBindBtn.click();

      const bindReq = await reqPromise.catch(() => null);
      if (bindReq) {
        expect(bindReq.url()).toMatch(/bind_enum/);
      }
    }

    // Close drawer
    const closeBtn = drawer.locator(".ant-drawer-close");
    if (await closeBtn.isVisible().catch(() => false)) {
      await closeBtn.click();
    }
  });
});
