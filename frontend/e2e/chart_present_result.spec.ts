/* ════════════════════════════════════════════════════════════════════════════
 *  Stage 6 Task 1 — present_result 新 final_answer wire 渲染验证 (mock SSE)
 * ----------------------------------------------------------------------------
 *  后端 Stage 3 产出完整 chart_option (2 series, x 去重), 前端 ChartRenderer
 *  优先用后端 chart_option → 直接渲染 ECharts (主路径零改动).
 *  产品化红线: 仅用通用词 (north/south/region).
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

test("present_result line chart with backend chart_option renders ECharts", async ({ page }) => {
  await bootstrap(page);
  const chartOption = {
    xAxis: { type: "category", data: ["2024-01-01", "2024-01-02"] },
    yAxis: { type: "value" },
    series: [
      { name: "north", type: "line", data: [10, 11] },
      { name: "south", type: "line", data: [20, 21] },
    ],
    tooltip: { trigger: "axis" },
    legend: { data: ["north", "south"] },
  };
  await page.route("**/api/query/stream", (route: Route) =>
    route.fulfill({
      status: 200,
      headers: { ...SSE_HEADERS },
      body: sseBody([
        { event: "agent_started", data: { trace_id: "t", started_at: "now" } },
        {
          event: "final_answer",
          data: {
            content: "已生成折线图",
            history_id: 1,
            stop_reason: "end_turn",
            rows: [
              { day: "2024-01-01", region: "north", amount: 10 },
              { day: "2024-01-01", region: "south", amount: 20 },
              { day: "2024-01-02", region: "north", amount: 11 },
              { day: "2024-01-02", region: "south", amount: 21 },
            ],
            columns: ["day", "region", "amount"],
            chart_type: "line",
            chart_option: chartOption,
            category_column: "day",
            truncated: false,
            rendered_row_count: 4,
            total_row_count: 4,
          },
        },
      ]),
    }),
  );
  const input = page.getByPlaceholder(/输入统计需求/);
  await input.fill("按日对比两地区销量");
  await input.press("Enter");
  await expect(page.locator("canvas").first()).toBeVisible();
});
