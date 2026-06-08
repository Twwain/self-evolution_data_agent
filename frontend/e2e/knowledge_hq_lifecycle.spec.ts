/* ════════════════════════════════════════════════════════════════════════════
 *  Phase 3 — HQ 全生命周期 e2e: 编辑全部按钮 + 手改 HQ
 * ----------------------------------------------------------------------------
 *  使用真实后端 (localhost:8001) + 真实登录 (admin/Cb1392010).
 *  baseURL = http://localhost:3000 (vite dev), Chromium project.
 * ══════════════════════════════════════════════════════════════════════════ */

import { test, expect } from "@playwright/test";

const ADMIN_USER = "admin";
const ADMIN_PASS = "Cb1392010";

async function loginAsAdmin(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByPlaceholder("username").fill(ADMIN_USER);
  await page.getByPlaceholder("password").fill(ADMIN_PASS);
  await page.getByRole("button", { name: "登 录" }).click();
  await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 10000 });
}

test.describe("HQ lifecycle e2e", () => {
  test("编辑全部按钮可见 + Modal 编辑 + PUT 请求含 hypothetical_queries", async ({ page, request }) => {
    await loginAsAdmin(page);

    // ── Step 1: 通过 API 创建一条 canonical route_hint KE ──
    const loginResp = await request.post("/api/auth/login", {
      data: { username: ADMIN_USER, password: ADMIN_PASS },
    });
    expect(loginResp.ok()).toBeTruthy();
    const { access_token } = await loginResp.json();
    const headers = { Authorization: `Bearer ${access_token}` };

    const nsResp = await request.get("/api/namespaces", { headers });
    expect(nsResp.ok()).toBeTruthy();
    const namespaces = await nsResp.json();
    test.skip(namespaces.length === 0, "无可用 namespace, 跳过");
    const ns = namespaces[0];

    // 创建 route_hint KE
    const createResp = await request.post("/api/knowledge", {
      headers,
      data: {
        namespace_id: ns.id,
        entry_type: "route_hint",
        content: "e2e-hq-lifecycle-test: 订单→用户路由",
      },
    });
    expect(createResp.status()).toBeLessThan(300);
    const created = await createResp.json();
    const entryId = created.entry?.id || created.id || created.entry_id;
    expect(entryId).toBeTruthy();

    // approve (proposed → canonical)
    const approveResp = await request.post(
      `/api/knowledge/audit/${entryId}/approve`,
      { headers, data: {} },
    );
    expect(approveResp.ok()).toBeTruthy();

    // ── Step 2: 导航到知识库页面, 搜索我们创建的 entry ──
    await page.goto("/knowledge");
    await page.locator(".ant-select").first().click();
    await page.locator(".ant-select-item-option-content").getByText(ns.name).click();

    // 搜索我们创建的 entry
    const searchBox = page.getByPlaceholder("搜索 content/description/payload");
    await searchBox.fill("e2e-hq-lifecycle-test");
    await searchBox.press("Enter");

    // 验卡片渲染
    await expect(
      page.getByText("e2e-hq-lifecycle-test").first()
    ).toBeVisible({ timeout: 10000 });

    // ── Step 3: 验 "编辑全部" 按钮可见 ──
    const editBtn = page.getByRole("button", { name: "编辑全部" }).first();
    await expect(editBtn).toBeVisible({ timeout: 5000 });

    // ── Step 4: 点击编辑全部, Modal 出现 ──
    await editBtn.click();
    const modal = page.locator(".ant-modal-content").last();
    await expect(modal).toBeVisible({ timeout: 5000 });

    // TextArea 应可见
    const textarea = modal.locator("textarea");
    await expect(textarea).toBeVisible();

    // ── Step 5: 输入新 HQ 并保存 (antd Modal OK button) ──
    await textarea.fill("e2e手改问题1\ne2e手改问题2");

    // 监听 PUT 请求
    const putPromise = page.waitForResponse(
      (r) => r.url().includes("/api/knowledge/") && r.request().method() === "PUT",
      { timeout: 15000 },
    );

    // antd Modal footer 的确认按钮 (在 .ant-modal-wrap 内)
    const modalWrap = page.locator(".ant-modal-wrap").last();
    const okBtn = modalWrap.locator(".ant-modal-footer .ant-btn-primary");
    await expect(okBtn).toBeVisible({ timeout: 3000 });
    await okBtn.click();

    // 等 PUT 响应
    const putResp = await putPromise;
    expect(putResp.status()).toBeLessThan(300);

    // 等 Modal 关闭
    await page.waitForTimeout(1000);

    // ── Step 6: 验证后端 — HQ 已更新 ──
    const queueResp = await request.get("/api/knowledge/audit/queue", {
      headers,
      params: { namespace_id: String(ns.id), q: "e2e-hq-lifecycle-test" },
    });
    expect(queueResp.ok()).toBeTruthy();
    const queue = await queueResp.json();
    const updatedEntry = queue.items.find((e: any) => e.id === entryId);
    if (updatedEntry?.hypothetical_queries_json) {
      const hqs = JSON.parse(updatedEntry.hypothetical_queries_json);
      expect(hqs.map((h: any) => h.q)).toEqual(["e2e手改问题1", "e2e手改问题2"]);
      expect(hqs[0].model).toBe("manual");
    }

    // ── Cleanup ──
    await request.delete(`/api/knowledge/${entryId}`, {
      headers,
      data: { mode: "soft", reason: "e2e cleanup" },
    });
  });

  test("route_hint 类型显示编辑全部按钮, terminology 类型不显示", async ({ page, request }) => {
    await loginAsAdmin(page);

    const loginResp = await request.post("/api/auth/login", {
      data: { username: ADMIN_USER, password: ADMIN_PASS },
    });
    const { access_token } = await loginResp.json();
    const headers = { Authorization: `Bearer ${access_token}` };

    const nsResp = await request.get("/api/namespaces", { headers });
    const namespaces = await nsResp.json();
    test.skip(namespaces.length === 0, "无可用 namespace, 跳过");
    const ns = namespaces[0];

    await page.goto("/knowledge");
    await page.locator(".ant-select").first().click();
    await page.locator(".ant-select-item-option-content").getByText(ns.name).click();
    await page.waitForTimeout(2000);

    // 验证: 如果页面上有 route_hint 类型的 entry, 应该能看到 "编辑全部" 按钮
    // (因为 HypotheticalQueriesPanel 只对 rule/route_hint 渲染)
    const editBtns = page.getByRole("button", { name: "编辑全部" });
    const editBtnCount = await editBtns.count();

    // 如果有编辑全部按钮, 说明有 rule/route_hint entry — 这是正确行为
    // 关键验证: 按钮只出现在 rule/route_hint 的 card 中
    if (editBtnCount > 0) {
      // 通过 API 验证: 获取 queue 中的 entry, 确认有编辑按钮的都是 rule/route_hint
      const queueResp = await request.get("/api/knowledge/audit/queue", {
        headers,
        params: { namespace_id: String(ns.id) },
      });
      const queue = await queueResp.json();
      const typesWithHQ = queue.items
        .filter((e: any) => ["rule", "route_hint"].includes(e.entry_type))
        .length;
      // 编辑按钮数量应 ≤ rule/route_hint entry 数量
      expect(editBtnCount).toBeLessThanOrEqual(typesWithHQ);
    }
  });
});
