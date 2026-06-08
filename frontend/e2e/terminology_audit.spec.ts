/* ════════════════════════════════════════════════════════════════════════════
 *  Phase 3 Task 3.4 — Playwright e2e: terminology 编辑 + 审核保存闭环
 * ----------------------------------------------------------------------------
 *  路径: canonical 状态 terminology KE → 编辑 → TerminologyEditPanel
 *        → database 一级 → collection 二级联动 → 改 term → 保存
 *
 *  关键 mock:
 *    GET  /api/namespaces/1/databases       → [{db_q, mongodb, ds=2}]
 *    GET  /api/namespaces/1/collections     → {db_q, mongodb, [c_category]}
 *    POST /api/knowledge/audit/conflict-preview → {conflicts: []}
 *    PUT  /api/knowledge/77                 → 保存成功
 *
 *  注: AuditCard 的 "编辑" 按钮只在 status=canonical 时出现 (proposed 仅有
 *  通过/拒绝). 故走"待审"队列时把 status 标 canonical, 用 audit-pending tab
 *  的同一队列接口拿到该卡片 — backend status 字段控制按钮渲染.
 *
 *  验收点:
 *    1. database Select 可见且能选 db_q
 *    2. db_type Input disabled 且自动同步 'mongodb'
 *    3. collection Select 选 c_category
 *    4. term Input 改为 '商品'
 *    5. PUT /api/knowledge/77 被调用 + 业务成功消息可见
 * ══════════════════════════════════════════════════════════════════════════ */

import { test, expect } from "@playwright/test";

// ── 复用 audit_v2.spec.ts 的注入登录态 + KnowledgePage tab 数据源 mock ──
async function setupKnowledgePage(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    localStorage.setItem("token", "fake-jwt-test-token");
    localStorage.setItem(
      "user",
      JSON.stringify({ id: 1, username: "admin", role: "admin", is_active: true }),
    );
  });

  await page.route("**/api/users/me", (r) =>
    r.fulfill({
      json: { id: 1, username: "admin", role: "admin", is_active: true, created_at: "2026-01-01" },
    }),
  );
  await page.route("**/api/namespaces", (r) =>
    r.fulfill({
      json: [{ id: 1, name: "测试空间", slug: "test", description: "", created_at: "2026-01-01" }],
    }),
  );
  await page.route("**/api/namespaces/1/knowledge", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/namespaces/1/repos", (r) => r.fulfill({ json: { repos: [] } }));
  await page.route("**/api/namespaces/1/mongo/canonical", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/namespaces/1/mongo/conflicts", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/namespaces/1/terminology/conflicts**", (r) =>
    r.fulfill({ json: { conflicts: [] } }),
  );
}

test.describe("terminology audit edit e2e", () => {
  test("canonical terminology → edit → database/collection cascade → save", async ({ page }) => {
    await setupKnowledgePage(page);

    // ── canonical 状态 terminology KE (AuditCard 显"编辑"按钮的前提) ──
    const initialPayload = {
      term: "货品",
      primary_database: "",
      primary_collection: "",
      db_type: "",
      synonyms: [],
    };
    const entry = {
      id: 77,
      namespace_id: 1,
      entry_type: "terminology",
      tier: "normal",
      content: JSON.stringify(initialPayload),
      raw_input: JSON.stringify(initialPayload),
      description: "",
      source: "manual",
      status: "canonical",
      is_superseded: false,
      reviewed: true,
      refined_at: null,
      created_at: "2026-05-01T00:00:00Z",
    };

    // 走 audit-pending tab 的队列, 但塞 canonical 数据触发"编辑"按钮渲染
    await page.route("**/api/knowledge/audit/queue*", (r) =>
      r.fulfill({ json: { items: [entry], total: 1, page: 1, size: 20 } }),
    );

    // ── terminology 联动 API ──
    let dbCalled = 0;
    await page.route("**/api/namespaces/1/databases", (r) => {
      dbCalled++;
      return r.fulfill({
        json: {
          databases: [
            { database: "db_q", db_type: "mongodb", datasource_id: 2, host: "localhost" },
          ],
        },
      });
    });
    let collCalled = 0;
    await page.route("**/api/namespaces/1/collections**", (r) => {
      collCalled++;
      return r.fulfill({
        json: { database: "db_q", db_type: "mongodb", collections: ["c_category"] },
      });
    });

    // ── debounce 冲突检测: 返空, 不阻塞保存 ──
    await page.route("**/api/knowledge/audit/conflict-preview", (r) =>
      r.fulfill({ json: { conflicts: [] } }),
    );

    // ── 保存提交 ──
    let putCalled = false;
    let putBody: any = null;
    await page.route("**/api/knowledge/77", (r) => {
      if (r.request().method() === "PUT") {
        putCalled = true;
        try { putBody = r.request().postDataJSON(); } catch { /* ignore */ }
        return r.fulfill({ json: { entry: { ...entry }, conflicts: [] } });
      }
      return r.fallback();
    });

    await page.goto("/knowledge");

    // 选 namespace
    await page.locator(".ant-select").first().click();
    await page.getByText("测试空间").click();

    // 切待审 tab — canonical entry 也会渲染因为 mock 队列直接给
    await page.getByRole("button", { name: /待审/ }).click();

    // 卡片渲染 + "编辑" 按钮可见 (canonical 状态分支)
    // 注: antd Button 在 EXACTLY 两个 CJK 字符间插入 U+2005, 故 "编辑" → "编 辑"
    await expect(page.getByText('"term":"货品"')).toBeVisible({ timeout: 5000 });
    await page.getByRole("button", { name: /^编.?辑$/ }).click();

    // Modal 内 TerminologyEditPanel
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // databases API 已被调
    await expect.poll(() => dbCalled, { timeout: 3000 }).toBeGreaterThan(0);

    // ── database Select: 点开 → 选 db_q ──
    // 注: antd Select 的 aria-label 同时挂在外层 div 和内部 input/combobox,
    //     用 getByRole("combobox") 唯一定位 input 避免 strict mode 冲突.
    //     option 是 portal 渲染, 但内部 div 是 inline-block, 用文本直选避免
    //     可见性判断对 dropdown 动画的敏感.
    await dialog.getByRole("combobox", { name: "database" }).click();
    await page.locator(".ant-select-item-option").filter({ hasText: "db_q" }).first().click();

    // collections API 已被调用
    await expect.poll(() => collCalled, { timeout: 3000 }).toBeGreaterThan(0);

    // ── db_type 自动同步为 'mongodb' (Input disabled, value 反映 readOnly 状态) ──
    await expect(dialog.getByLabel("db_type")).toHaveValue("mongodb", { timeout: 3000 });

    // ── collection Select: 选 c_category ──
    await dialog.getByRole("combobox", { name: "collection" }).click();
    await page.locator(".ant-select-item-option").filter({ hasText: "c_category" }).first().click();

    // ── term: 改为 '商品' ──
    const termInput = dialog.getByLabel("term");
    await termInput.fill("商品");

    // ── reason 必填 ──
    await dialog.getByPlaceholder("为何修改").fill("更新主集合到 c_category");

    // ── 保存 (antd Button 双 CJK 字符间 U+2005) ──
    await dialog.getByRole("button", { name: /^保.?存$/ }).click();

    // PUT /api/knowledge/77 已被调用
    await expect.poll(() => putCalled, { timeout: 3000 }).toBeTruthy();

    // body 包含 payload (terminology 走 payload field 而非 content)
    expect(putBody).toBeTruthy();
    expect(putBody.payload).toBeTruthy();
    expect(putBody.payload.term).toBe("商品");
    expect(putBody.payload.primary_database).toBe("db_q");
    expect(putBody.payload.primary_collection).toBe("c_category");
    expect(putBody.payload.db_type).toBe("mongodb");
    expect(putBody.reason).toBe("更新主集合到 c_category");

    // 业务成功消息
    await expect(page.getByText("已编辑")).toBeVisible({ timeout: 3000 });
  });
});
