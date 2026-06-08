/* ════════════════════════════════════════════════════════════════════════════
 *  Stage 2 抓手 D — Playwright e2e: RelatedEntriesPanel 渲染
 * ----------------------------------------------------------------------------
 *  Mock audit queue 含 entry + related_entry_ids_json (equivalent/supplement/conflict),
 *  验 colored tags 渲染.
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

test.describe("amem audit e2e", () => {
  test("RelatedEntriesPanel renders colored tags for entries with relations", async ({ page }) => {
    await setupKnowledgePage(page);

    const entryWithRelations = {
      id: 400,
      namespace_id: 1,
      entry_type: "rule",
      tier: "normal",
      content: "订单状态 paid 表示已支付",
      raw_input: "订单状态 paid 表示已支付",
      description: "",
      source: "agent_learn",
      status: "proposed",
      is_superseded: false,
      reviewed: false,
      refined_at: null,
      created_at: "2026-05-20T00:00:00Z",
      related_entry_ids_json: JSON.stringify([
        {
          related_entry_id: 100,
          relation: "equivalent",
          llm_reason: "语义等价: 两条规则描述相同业务含义",
          detected_at: "2026-05-20T01:00:00Z",
        },
        {
          related_entry_id: 101,
          relation: "supplement",
          llm_reason: "补充说明: 扩展了支付状态的细节",
          detected_at: "2026-05-20T01:00:00Z",
        },
        {
          related_entry_id: 102,
          relation: "conflict",
          llm_reason: "冲突: 与现有规则对 paid 的定义矛盾",
          detected_at: "2026-05-20T01:00:00Z",
        },
      ]),
    };

    await page.route("**/api/knowledge/audit/queue*", (r) =>
      r.fulfill({
        json: {
          items: [entryWithRelations],
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
    await expect(page.getByText("订单状态 paid 表示已支付")).toBeVisible({ timeout: 5000 });

    // 验 relation tags 渲染 (colored: blue=等价, green=补充, red=冲突)
    await expect(page.getByText("≡ 等价")).toBeVisible();
    await expect(page.getByText("+ 补充")).toBeVisible();
    await expect(page.getByText("⚠ 冲突")).toBeVisible();

    // 验 "查看详情" 按钮存在
    await expect(page.getByRole("button", { name: /查看详情/ }).first()).toBeVisible();
  });
});
