/* ════════════════════════════════════════════════════════════════════════════
 *  Stage 2 抓手 A — Playwright e2e: HypotheticalQueriesPanel 渲染
 * ----------------------------------------------------------------------------
 *  Mock audit queue 含 rule entry + hypothetical_queries_json,
 *  验 HyQE 蓝色 tag 渲染.
 *  baseURL = http://localhost:3001 (vite preview), Chromium project.
 * ══════════════════════════════════════════════════════════════════════════ */

import { test, expect } from "@playwright/test";

async function setupKnowledgePage(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    localStorage.setItem("token", "fake-jwt-test-token");
    localStorage.setItem(
      "user",
      JSON.stringify({
        id: 1,
        username: "admin",
        role: "admin",
        is_active: true,
      }),
    );
  });

  await page.route("**/api/users/me", (r) =>
    r.fulfill({
      json: {
        id: 1,
        username: "admin",
        role: "admin",
        is_active: true,
        created_at: "2026-01-01",
      },
    }),
  );
  await page.route("**/api/namespaces", (r) =>
    r.fulfill({
      json: [
        { id: 1, name: "测试空间", slug: "test", description: "", created_at: "2026-01-01" },
      ],
    }),
  );
  await page.route("**/api/namespaces/1/knowledge", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/namespaces/1/repos", (r) =>
    r.fulfill({ json: { repos: [] } }),
  );
  await page.route("**/api/namespaces/1/mongo/canonical", (r) =>
    r.fulfill({ json: [] }),
  );
  await page.route("**/api/namespaces/1/mongo/conflicts", (r) =>
    r.fulfill({ json: [] }),
  );
}

test.describe("hyqe audit e2e", () => {
  test("HypotheticalQueriesPanel renders blue tags for rule entry", async ({ page }) => {
    await setupKnowledgePage(page);

    const ruleEntry = {
      id: 300,
      namespace_id: 1,
      entry_type: "rule",
      tier: "normal",
      content: "活跃用户指 30 天内有过登录的用户",
      raw_input: "活跃用户指 30 天内有过登录的用户",
      description: "",
      source: "agent_learn",
      status: "proposed",
      is_superseded: false,
      reviewed: false,
      refined_at: null,
      created_at: "2026-05-20T00:00:00Z",
      hypothetical_queries_json: JSON.stringify([
        { q: "本月活跃用户有多少", generated_at: "2026-05-20T01:00:00Z", model: "qwen-plus" },
        { q: "最近30天登录过的用户数量", generated_at: "2026-05-20T01:00:00Z", model: "qwen-plus" },
        { q: "活跃用户统计", generated_at: "2026-05-20T01:00:00Z", model: "qwen-plus" },
      ]),
    };

    await page.route("**/api/knowledge/audit/queue*", (r) =>
      r.fulfill({
        json: {
          items: [ruleEntry],
          total: 1,
          page: 1,
          size: 20,
        },
      }),
    );

    await page.goto("/knowledge");

    // 选 namespace (antd Select dropdown option)
    await page.locator(".ant-select").first().click();
    await page.locator(".ant-select-item-option-content").getByText("测试空间").click();

    // 切待审 tab
    await page.getByRole("button", { name: /待审/ }).click();

    // 验卡片渲染
    await expect(page.getByText("活跃用户指 30 天内有过登录的用户")).toBeVisible({ timeout: 5000 });

    // 验 hypothetical queries 蓝色 tag 渲染
    await expect(page.getByText("本月活跃用户有多少")).toBeVisible();
    await expect(page.getByText("最近30天登录过的用户数量")).toBeVisible();
    await expect(page.getByText("活跃用户统计")).toBeVisible();
  });
});
