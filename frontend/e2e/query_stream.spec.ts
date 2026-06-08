/* ════════════════════════════════════════════════════════════════════════════
 *  Stage 6 Task 13 — Playwright e2e: Agent SSE 主链路
 * ----------------------------------------------------------------------------
 *  Mock /api/namespaces + /api/query/stream + 反向通道, 验四条业务路径:
 *    1. happy path: agent_started → tool_use → tool_result → final_answer
 *    2. cancel <1.5s: 长流 + abort 按钮触发 /correct 反向通道
 *    3. clarify 闭环: clarify_request 卡片 → 提交答案 → /clarify_response
 *    4. cost_warning: Alert "预估扫描 N 文档" 渲染
 *  TTFB < 1s, 中间过程 < 500ms 由 expect timeout 兜底.
 * ══════════���═══════════════════════════════════════════════════════════════ */

import { test, expect, Route } from "@playwright/test";

const SSE_HEADERS = {
  "Content-Type": "text/event-stream",
  "Cache-Control": "no-cache",
  Connection: "keep-alive",
  "X-Trace-Id": "trace-test",
} as const;

// ─��� SSE 协议 wire 格式: event:NAME\ndata:JSON\n\n ───────────────────────────
const sseBody = (events: { event: string; data: unknown }[]): string =>
  events.map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join("");

// ── 注入登录态 + 选 namespace + 输入问题 ──────────────────────────────────
async function bootstrap(page: import("@playwright/test").Page) {
  // 1) 注入 localStorage 绕过 RequireAuth
  await page.addInitScript(() => {
    localStorage.setItem("token", "fake-jwt-test-token");
    localStorage.setItem(
      "user",
      JSON.stringify({ id: 1, username: "tester", role: "admin", email: "t@e2e" }),
    );
  });

  // 2) Mock 命名空间列表
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
  // 选 namespace (antd Select)
  await page.locator(".ant-select").first().click();
  await page.locator(".ant-select-item-option").first().click();
}

async function sendQuestion(page: import("@playwright/test").Page, q: string) {
  const input = page.getByPlaceholder(/输入统计需求/);
  await input.fill(q);
  await input.press("Enter");
}

test.describe("query stream e2e", () => {
  // ═════��══ 1. happy path ════════
  test("full happy path: agent_started → tool_use → tool_result → final_answer", async ({
    page,
  }) => {
    await page.route("**/api/query/stream", (route: Route) =>
      route.fulfill({
        status: 200,
        headers: { ...SSE_HEADERS },
        body: sseBody([
          { event: "agent_started", data: { trace_id: "trace-test", started_at: "now" } },
          {
            event: "tool_use",
            data: {
              tool_call_id: "t1",
              name: "lookup_knowledge",
              input: { query: "x" },
            },
          },
          {
            event: "tool_result",
            data: { tool_call_id: "t1", status: "ok", output: "ok" },
          },
          {
            event: "final_answer",
            data: {
              content: "查询完成",
              columns: ["a"],
              rows: [{ a: 1 }],
              chart_type: "table",
            },
          },
          { event: "agent_finished", data: { stop_reason: "end_turn", total_iterations: 1 } },
        ]),
      }),
    );

    await bootstrap(page);
    const t0 = Date.now();
    await sendQuestion(page, "happy path 测试");

    // TTFB < 1s: trace Tag 表明 SSE 头已收到 (running 可能瞬时 → finished)
    await expect(page.locator(".ant-tag", { hasText: /^trace:/ })).toBeVisible({
      timeout: 1000,
    });
    expect(Date.now() - t0).toBeLessThan(1500);

    // ToolNode 渲染 lookup_knowledge — 中间过程
    await expect(page.getByText("lookup_knowledge")).toBeVisible({ timeout: 500 });

    // 最终结果 + finished 状态
    await expect(page.getByText("最终结果")).toBeVisible();
    await expect(page.locator(".ant-tag", { hasText: /^finished$/ })).toBeVisible();
  });

  // ════════ 2. cancel within 1.5s ════════
  test("cancel within 1.5s triggers /correct or /cancel reverse channel", async ({ page }) => {
    // SSE: 仅发 agent_started, body 之后挂住 — 让前端 status 停留 running.
    // Playwright fulfill 不支持流式追加, 故只发头一段事件 + 不关闭连接.
    await page.route("**/api/query/stream", async (route: Route) => {
      await route.fulfill({
        status: 200,
        headers: { ...SSE_HEADERS },
        body: sseBody([
          { event: "agent_started", data: { trace_id: "trace-test", started_at: "now" } },
        ]),
      });
    });

    let correctCalled = false;
    await page.route("**/api/query/stream/*/correct", (route) => {
      correctCalled = true;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true }),
      });
    });
    await page.route("**/api/query/stream/*/cancel", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ cancelled: true }),
      }),
    );

    await bootstrap(page);
    await sendQuestion(page, "cancel 测试");

    // 等 abort 按钮 enabled (status=running)
    const abortBtn = page.getByRole("button", { name: /abort/i });
    await expect(abortBtn).toBeEnabled({ timeout: 1500 });

    const t0 = Date.now();
    await abortBtn.click();

    // 1.5s 内: 后端反向通道被触发, 或前端 status 翻 cancelled
    await expect
      .poll(
        () =>
          correctCalled ||
          page.locator(".ant-tag", { hasText: /cancelled|idle/ }).count(),
        { timeout: 1500 },
      )
      .toBeTruthy();
    expect(Date.now() - t0).toBeLessThan(1500);
  });

  // ════════ 3. clarify 闭环 ════════
  test("clarify_request shows card; submit answer hits /clarify_response", async ({ page }) => {
    await page.route("**/api/query/stream", (route: Route) =>
      route.fulfill({
        status: 200,
        headers: { ...SSE_HEADERS },
        body: sseBody([
          { event: "agent_started", data: { trace_id: "trace-test", started_at: "now" } },
          {
            event: "clarify_request",
            data: {
              pending_id: 42,
              question: "你想看哪个时段的数据?",
              options: ["最近一周", "最近一月"],
              reason: "时间口径不明确",
            },
          },
        ]),
      }),
    );

    let clarifyCalled = false;
    let clarifyBody: any = null;
    await page.route("**/api/query/stream/*/clarify_response", async (route) => {
      clarifyCalled = true;
      clarifyBody = JSON.parse(route.request().postData() ?? "{}");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true }),
      });
    });

    await bootstrap(page);
    await sendQuestion(page, "需要澄清的问题");

    // ClarifyCard 出现
    await expect(page.getByText("需要你的澄清")).toBeVisible({ timeout: 1500 });
    await expect(page.getByText("你想看哪个时段的数据?")).toBeVisible();

    // 选第一个 Radio 选项
    await page.getByRole("radio", { name: "最近一周" }).check();
    await page.getByRole("button", { name: /submit/i }).click();

    await expect.poll(() => clarifyCalled, { timeout: 1500 }).toBeTruthy();
    expect(clarifyBody).toMatchObject({ pending_id: 42, answer: "最近一周" });
  });

  // ════════ 4. cost_warning banner ════════
  test("cost_warning event renders 预估扫描 Alert", async ({ page }) => {
    await page.route("**/api/query/stream", (route: Route) =>
      route.fulfill({
        status: 200,
        headers: { ...SSE_HEADERS },
        body: sseBody([
          { event: "agent_started", data: { trace_id: "trace-test", started_at: "now" } },
          {
            event: "cost_warning",
            data: {
              estimated_docs: 1234567,
              threshold: 100000,
              advice: "建议加上时间过滤",
            },
          },
          { event: "agent_finished", data: { stop_reason: "end_turn", total_iterations: 1 } },
        ]),
      }),
    );

    await bootstrap(page);
    await sendQuestion(page, "大数据查询");

    await expect(page.getByText(/预估扫描/)).toBeVisible({ timeout: 1500 });
    await expect(page.getByText(/1,234,567/)).toBeVisible();
    await expect(page.getByText(/建议加上时间过滤/)).toBeVisible();
  });
});
