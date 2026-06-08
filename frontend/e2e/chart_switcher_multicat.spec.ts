/* ════════════════════════════════════════════════════════════════════════════
 *  图表切换按钮 — 多分类维度回归 (trace 7270955a)
 * ----------------------------------------------------------------------------
 *  后端 visualizer 对 ≥2 个分类维度的数据返回 chart_type=table (2D 图无法无损
 *  表达). 前端 ResultDisplay 在 chart_type ∈ {table, card} 时只保留对应单一视图,
 *  隐藏 折线图 / 柱状图 / 饼图 切换按钮.
 *
 *  验两条路径:
 *    1. 多分类 (brand × itemType) + chart_type=table → 仅「表格」按钮可见
 *    2. 单分类 (name × count) + chart_type=bar → 四个按钮全可见 (不误伤)
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
      body: JSON.stringify([
        { id: 1, name: "测试命名空间", slug: "test-ns", description: "" },
      ]),
    }),
  );
  await page.goto("/");
  await page.locator(".ant-select").first().click();
  await page.locator(".ant-select-item-option").first().click();
}

async function sendQuestion(page: import("@playwright/test").Page, q: string) {
  const input = page.getByPlaceholder(/输入统计需求/);
  await input.fill(q);
  await input.press("Enter");
}

function mockFinalAnswer(
  page: import("@playwright/test").Page,
  finalData: Record<string, unknown>,
) {
  return page.route("**/api/query/stream", (route: Route) =>
    route.fulfill({
      status: 200,
      headers: { ...SSE_HEADERS },
      body: sseBody([
        { event: "agent_started", data: { trace_id: "trace-test", started_at: "now" } },
        { event: "final_answer", data: finalData },
        { event: "agent_finished", data: { stop_reason: "end_turn", total_iterations: 1 } },
      ]),
    }),
  );
}

test.describe("chart switcher — 多分类维度", () => {
  // ════════ 1. 多分类 → table, 仅「表格」按钮可见 ════════
  test("multi-category result (chart_type=table) hides line/bar/pie buttons", async ({
    page,
  }) => {
    await mockFinalAnswer(page, {
      content: "查询完成",
      columns: ["brandName", "itemTypeName", "resourceCount"],
      rows: [
        { brandName: "优选 A 级标准版", itemTypeName: "条目", resourceCount: 1019 },
        { brandName: "优选 A 级标准版", itemTypeName: "短文", resourceCount: 28 },
        { brandName: "优选 B 级标准版", itemTypeName: "条目", resourceCount: 500 },
      ],
      chart_type: "table",
      category_column: "brandName",
    });

    await bootstrap(page);
    await sendQuestion(page, "多分类维度查询");

    await expect(page.getByText("最终结果")).toBeVisible();

    // 「表格」按钮可见, 其余三个隐藏
    await expect(page.getByRole("button", { name: "表格", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "折线图", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "柱状图", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "饼图", exact: true })).toHaveCount(0);

    // 资源类型这一维在表格里完整可见 (截图里丢失的信息)
    await expect(
      page.getByRole("columnheader", { name: "itemTypeName" }),
    ).toBeVisible();
    await expect(page.getByText("条目").first()).toBeVisible();
  });

  // ════════ 2. 单分类 → bar, 四个按钮全可见 (不误伤) ════════
  test("single-category result (chart_type=bar) keeps all switch buttons", async ({
    page,
  }) => {
    await mockFinalAnswer(page, {
      content: "查询完成",
      columns: ["name", "count"],
      rows: [
        { name: "a", count: 1 },
        { name: "b", count: 2 },
        { name: "c", count: 3 },
      ],
      chart_type: "bar",
      category_column: "name",
    });

    await bootstrap(page);
    await sendQuestion(page, "单分类维度查询");

    await expect(page.getByText("最终结果")).toBeVisible();

    await expect(page.getByRole("button", { name: "表格", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "折线图", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "柱状图", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "饼图", exact: true })).toBeVisible();
  });
});
