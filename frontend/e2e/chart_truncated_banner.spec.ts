/* ════════════════════════════════════════════════════════════════════════════
 *  Stage 6 Task 3 — 截断 banner (truncated wire → 显式提示, 绝不静默)
 * ----------------------------------------------------------------------------
 *  final_answer 带 truncated=true + rendered/total → ResultDisplay 显示截断 banner.
 * ══════════════════════════════════════════════════════════════════════════ */

import { test, expect, Route } from "@playwright/test";

const SSE_HEADERS = {
  "Content-Type": "text/event-stream",
  "Cache-Control": "no-cache",
  Connection: "keep-alive",
  "X-Trace-Id": "trace-test",
} as const;

const sseBody = (events: { event: string; data: unknown }[]): string =>
  events.map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join("");

async function bootstrap(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    localStorage.setItem("token", "fake-jwt-test-token");
    localStorage.setItem(
      "user",
      JSON.stringify({ id: 1, username: "tester", role: "admin", email: "t@e2e" }),
    );
  });
  await page.route("**/api/namespaces", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{ id: 1, name: "测试命名空间", slug: "test-ns", description: "" }]),
    }),
  );
  await page.goto("/");
  await page.locator(".ant-select").first().click();
  await page.locator(".ant-select-item-option").first().click();
}

test("truncated final_answer shows explicit banner with total/rendered counts", async ({ page }) => {
  await bootstrap(page);
  await page.route("**/api/query/stream", (route: Route) =>
    route.fulfill({
      status: 200,
      headers: { ...SSE_HEADERS },
      body: sseBody([
        { event: "agent_started", data: { trace_id: "t", started_at: "now" } },
        {
          event: "final_answer",
          data: {
            content: "结果已截断",
            history_id: 1,
            stop_reason: "end_turn",
            rows: [{ day: "2024-01-01", amount: 10 }],
            columns: ["day", "amount"],
            chart_type: "line",
            chart_option: {
              xAxis: { type: "category", data: ["2024-01-01"] },
              yAxis: { type: "value" },
              series: [{ name: "amount", type: "line", data: [10] }],
            },
            category_column: "day",
            truncated: true,
            rendered_row_count: 500,
            total_row_count: 1462,
          },
        },
      ]),
    }),
  );
  const input = page.getByPlaceholder(/输入统计需求/);
  await input.fill("按日折线");
  await input.press("Enter");
  await expect(page.getByText("结果已截断")).toBeVisible();
  await expect(page.getByText(/结果共 1462 行/)).toBeVisible();
  await expect(page.getByText(/仅展示前 500 行/)).toBeVisible();
});
