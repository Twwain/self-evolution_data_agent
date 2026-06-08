/* ════════════════════════════════════════════════════════════════════════════
 *  方案 A 验收 — 多轮会话历史: 第一轮取消后再次查询, 第一轮内容不被覆盖
 * ----------------------------------------------------------------------------
 *  复现 bug: 单 AgentStreamState + start() 内部 reset 清空上一轮 → 历史丢失。
 *  修复: QueryPage 维护 turns[], 新一轮开始前归档当前非 idle 轮为只读历史。
 *  验收: 第一轮以 cancelled 收尾 → 提交第二轮 → 第一轮的问题文本 + cancelled
 *        状态仍可见, 且与第二轮 (finished) 同屏, 呈现两个不同 trace tag。
 *
 *  注: 第一轮的 cancelled 由 SSE `cancelled` 事件投递 (后端处理取消后的真实行为)。
 *      取消按钮 → abort 的前端路径已在 query_stream.spec.ts 覆盖; 且前端 reducer
 *      仅在收到 cancelled 事件时翻 status, abort 本身不改状态。Playwright fulfill
 *      为一次性响应, 无法在点击后再追加事件, 故此处用 SSE 事件确定性地表达取消态。
 * ══════════════════════════════════════════════════════════════════════════ */

import { test, expect, Route } from "@playwright/test";

const SSE_HEADERS = {
  "Content-Type": "text/event-stream",
  "Cache-Control": "no-cache",
  Connection: "keep-alive",
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

async function sendQuestion(page: import("@playwright/test").Page, q: string) {
  const input = page.getByPlaceholder(/输入统计需求/);
  await input.fill(q);
  await input.press("Enter");
}

test.describe("multi-turn history (方案 A)", () => {
  test("cancelled turn 1 then submit turn 2 — turn 1 content preserved on screen", async ({
    page,
  }) => {
    const Q1 = "第一轮查询A 级品牌";
    const Q2 = "第二轮查询B 级品牌";

    // 每次 /query/stream 返回不同 trace_id:
    //   第一轮: agent_started → cancelled  (确定性 cancelled 态)
    //   第二轮: agent_started → final_answer → agent_finished (finished 态)
    let streamCall = 0;
    await page.route("**/api/query/stream", async (route: Route) => {
      streamCall += 1;
      if (streamCall === 1) {
        await route.fulfill({
          status: 200,
          headers: { ...SSE_HEADERS, "X-Trace-Id": "trace-aaaa1111" },
          body: sseBody([
            { event: "agent_started", data: { trace_id: "trace-aaaa1111", started_at: "now" } },
            { event: "cancelled", data: {} },
          ]),
        });
      } else {
        await route.fulfill({
          status: 200,
          headers: { ...SSE_HEADERS, "X-Trace-Id": "trace-bbbb2222" },
          body: sseBody([
            { event: "agent_started", data: { trace_id: "trace-bbbb2222", started_at: "now" } },
            {
              event: "final_answer",
              data: { content: "第二轮完成", columns: ["a"], rows: [{ a: 1 }], chart_type: "table" },
            },
            { event: "agent_finished", data: { stop_reason: "end_turn", total_iterations: 1 } },
          ]),
        });
      }
    });

    await bootstrap(page);

    // ── Turn 1: 提交 → 等 cancelled 收尾 ──
    await sendQuestion(page, Q1);
    await expect(page.getByText(Q1)).toBeVisible({ timeout: 1500 });
    await expect(page.locator(".ant-tag", { hasText: /cancelled/ })).toBeVisible({ timeout: 1500 });

    // ── Turn 2: 再次提交 ──
    await sendQuestion(page, Q2);
    await expect(page.getByText(Q2)).toBeVisible({ timeout: 1500 });
    await expect(page.getByText("第二轮完成")).toBeVisible({ timeout: 1500 });

    // ── 验收核心: Q1 历史未被覆盖, 与 Q2 同时可见 ──
    await expect(page.getByText(Q1)).toBeVisible();
    await expect(page.getByText(Q2)).toBeVisible();
    // 两个不同 trace tag 同屏 (归档历史轮 + 活跃轮)
    await expect(page.locator(".ant-tag", { hasText: /trace: trace-aa/ })).toBeVisible();
    await expect(page.locator(".ant-tag", { hasText: /trace: trace-bb/ })).toBeVisible();
    // 历史轮仍标记 cancelled, 活跃轮 finished
    await expect(page.locator(".ant-tag", { hasText: /cancelled/ })).toBeVisible();
    await expect(page.locator(".ant-tag", { hasText: /finished/ })).toBeVisible();
  });
});
