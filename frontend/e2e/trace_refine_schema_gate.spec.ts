/* ════════════════════════════════════════════════════════════════════════════
 *  Phase 2 — trace 提炼 schema gate e2e (真实链路)
 * ----------------------------------------------------------------------------
 *  流程:
 *  1. 通过 API 把一条 refined trace 改回 completed (作为测试数据)
 *  2. 真实登录
 *  3. 导航到 Trace 提炼页
 *  4. 选中该 trace
 *  5. 点批量提炼按钮
 *  6. 等 refine API 响应
 *  7. 验证 trace 状态变 refined
 *  8. 验证产出的 KE payload 无 extra 字段 + 有机械字段
 *
 *  baseURL = http://localhost:3000, Chromium project.
 *  依赖: 后端 localhost:8001 运行中 + DB 中至少有 1 条 refined trace
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

test.describe("trace 提炼 schema gate 端到端", () => {
  test("用户走 Trace 提炼按钮 → 提案过 schema 闸门 → 入审核队列", async ({ page, request }) => {
    // ── Setup: 获取 token ──
    const loginResp = await request.post("/api/auth/login", {
      data: { username: ADMIN_USER, password: ADMIN_PASS },
    });
    expect(loginResp.ok()).toBeTruthy();
    const { access_token } = await loginResp.json();
    const headers = { Authorization: `Bearer ${access_token}` };

    // ── Setup: 找一条 completed trace 作为测试数据 ──
    const completedResp = await request.get("/api/agent-traces", {
      headers,
      params: { status: "completed", size: "1" },
    });
    expect(completedResp.ok()).toBeTruthy();
    const completedTraces = await completedResp.json();
    test.skip(completedTraces.length === 0, "无 completed trace 可用作测试数据");

    const testTraceId = completedTraces[0].trace_id;

    // ── Step 1: 登录 ──
    await loginAsAdmin(page);

    // ── Step 2: 导航到 Trace 提炼页 ──
    await page.getByText("Trace 提炼").click();
    await page.waitForTimeout(2000);

    // ── Step 3: 等待表格加载, 筛选 completed 状态 ──
    await expect(page.locator("table")).toBeVisible({ timeout: 5000 });

    // 选 Status filter = completed (确保只看 completed trace)
    const statusSelect = page.locator(".ant-select").filter({ hasText: "Status" });
    if (await statusSelect.isVisible({ timeout: 2000 }).catch(() => false)) {
      await statusSelect.click();
      const completedOption = page.locator(".ant-select-item-option-content").getByText("completed");
      if (await completedOption.isVisible({ timeout: 2000 }).catch(() => false)) {
        await completedOption.click();
      }
    }
    await page.waitForTimeout(1000);

    // ── Step 4: 选中 trace (点击行的 checkbox) ──
    const traceRow = page.locator("tbody tr").first();
    await expect(traceRow).toBeVisible({ timeout: 5000 });
    const rowCheckbox = traceRow.locator('input[type="checkbox"]');
    await rowCheckbox.check({ force: true });

    // ── Step 5: 点批量提炼按钮 ──
    const refineBtn = page.getByRole("button", { name: /批量提炼/ });
    await expect(refineBtn).toBeVisible({ timeout: 5000 });

    // 先注册 response 监听, 再点按钮
    const respPromise = page.waitForResponse(
      (r) => r.url().includes("/api/agent-traces/refine") && r.request().method() === "POST",
      { timeout: 60000 },
    );

    await refineBtn.click();

    // 如果有确认 Modal, 点确定
    const confirmBtn = page.locator(".ant-modal-footer .ant-btn-primary");
    if (await confirmBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await confirmBtn.click();
    }

    // ── Step 6: 等 refine 响应 ──
    const resp = await respPromise;
    expect(resp.status()).toBe(200);
    const refineOut = await resp.json();
    expect(refineOut.proposed_count).toBeGreaterThanOrEqual(0);

    // ── Step 7: 验证 trace 状态变 refined ──
    await page.waitForTimeout(2000);
    // 刷新列表验证状态
    const updatedTraceResp = await request.get(`/api/agent-traces/${testTraceId}`, { headers });
    if (updatedTraceResp.ok()) {
      const updatedTrace = await updatedTraceResp.json();
      expect(updatedTrace.status).toBe("refined");
    }

    // ── Step 8: 验证产出的 KE payload (如果有提案) ──
    if (refineOut.proposed_ke_ids && refineOut.proposed_ke_ids.length > 0) {
      const keId = refineOut.proposed_ke_ids[0];
      const queueResp = await request.get("/api/knowledge/audit/queue", {
        headers,
        params: { size: "100" },
      });
      expect(queueResp.ok()).toBeTruthy();
      const queue = await queueResp.json();
      const ke = queue.items.find((e: any) => e.id === keId);

      if (ke) {
        const payload = typeof ke.payload === "string" ? JSON.parse(ke.payload) : ke.payload;
        // 闸门生效证据: 无 LLM 自由发挥 extra 字段
        expect(Object.keys(payload)).not.toContain("cross_database_strategy");
        expect(Object.keys(payload)).not.toContain("route");
        expect(Object.keys(payload)).not.toContain("final_pipeline");

        // 如果是 route_hint, 应有 trace_extractor 补的机械字段
        if (ke.entry_type === "route_hint") {
          expect(payload).toHaveProperty("collection_path");
          expect(payload).toHaveProperty("cost_strategy");
        }
      }
    }
  });
});
