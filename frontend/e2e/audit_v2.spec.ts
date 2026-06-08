/* ════════════════════════════════════════════════════════════════════════════
 *  Stage 6 Task 14 — Playwright e2e: 审核 v2 闭环
 * ----------------------------------------------------------------------------
 *  Mock /api/namespaces + /api/knowledge/audit/* 验两条业务路径:
 *    1. queue → approve → log timeline 渲染 diff_json (Task 12 v2 视觉)
 *    2. 全选 5 条 + 批量通过 → BatchProgress 0/5 → 5/5 进度可见
 *  baseURL = http://localhost:3001 (vite preview), Chromium project.
 * ══════════════════════════════════════════════════════════════════════════ */

import { test, expect } from "@playwright/test";

// ── 测试夹具: localStorage 注入认证 + namespace 列表 + 各 tab 空 mock ──────
async function setupKnowledgePage(page: import("@playwright/test").Page) {
  // 1) 注入登录态绕过 RequireAuth + RequireAdmin
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

  // 2) namespace 主列表 + KnowledgePage.loadData 并发拉取的 4 个端点
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
        {
          id: 1,
          name: "测试空间",
          slug: "test",
          description: "",
          created_at: "2026-01-01",
        },
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

// ── KnowledgeEntry shape 工厂 ──────────────────────────────────────────────
const makeProposed = (id: number, content: string) => ({
  id,
  namespace_id: 1,
  entry_type: "terminology",
  tier: "normal",
  content,
  raw_input: content,
  description: "",
  source: "manual",
  status: "proposed",
  is_superseded: false,
  reviewed: false,
  refined_at: null,
  created_at: "2026-05-01T00:00:00Z",
});

test.describe("audit v2 e2e", () => {
  // ════════ 1. queue → approve → 审计日志 timeline diff 可见 ════════
  test("queue → approve → log timeline diff visible", async ({ page }) => {
    await setupKnowledgePage(page);

    // 待审队列: 1 条 proposed terminology "订单=c_product"
    await page.route("**/api/knowledge/audit/queue*", (r) =>
      r.fulfill({
        json: {
          items: [makeProposed(100, "订单=c_product")],
          total: 1,
          page: 1,
          size: 20,
        },
      }),
    );

    let approveCalled = false;
    await page.route("**/api/knowledge/audit/100/approve", (r) => {
      approveCalled = true;
      return r.fulfill({
        json: { ...makeProposed(100, "订单=c_product"), status: "canonical" },
      });
    });

    // 审计日志: 2 条记录, approve 行携带 diff_json (Task 12 v2 视觉锚点)
    await page.route("**/api/knowledge/audit/100/log", (r) =>
      r.fulfill({
        json: [
          {
            id: 1,
            entry_id: 100,
            actor_id: null,
            action: "propose",
            from_status: null,
            to_status: "proposed",
            reason: "",
            diff_json: "",
            created_at: "2026-05-01T01:00:00Z",
          },
          {
            id: 2,
            entry_id: 100,
            actor_id: 1,
            action: "approve",
            from_status: "proposed",
            to_status: "canonical",
            reason: "looks good",
            diff_json:
              '{"before":{"status":"proposed"},"after":{"status":"canonical"}}',
            created_at: "2026-05-01T02:00:00Z",
          },
        ],
      }),
    );

    await page.goto("/knowledge");

    // ── 选 namespace (antd Select) ──
    await page.locator(".ant-select").first().click();
    await page.getByText("测试空间").click();

    // ── 切待审 tab ──
    await page.getByRole("button", { name: /待审/ }).click();

    // ── 验卡片渲染 ──
    await expect(page.getByText("订单=c_product")).toBeVisible({ timeout: 5000 });

    // ── 点通过 → 后端 approve 被调用 ──
    // 注: antd Button 在 EXACTLY 两个 CJK 字符间自动插入 U+2005, 故用 regex 匹配
    await page.getByRole("button", { name: /^通.?过$/ }).click();
    await expect.poll(() => approveCalled, { timeout: 2000 }).toBeTruthy();

    // ── 打开审计日志 modal ──
    // 重新拉取后 mock 仍返同条 proposed, 卡片在 → 直接点本卡片的 "审计日志"
    await page.getByRole("button", { name: /审计日志/ }).click();

    // ── 验 timeline (modal 内部) ──
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 2000 });
    await expect(dialog.getByText("propose", { exact: true })).toBeVisible();
    await expect(dialog.getByText("approve", { exact: true })).toBeVisible();
    // 状态流转链: proposed → canonical (timeline children 内的 status 文本)
    // 用 first() 容多元素匹配 (diff_json 也含 "proposed" 字样)
    await expect(dialog.getByText(/proposed/).first()).toBeVisible();
    await expect(dialog.getByText(/canonical/).first()).toBeVisible();

    // ── 验 diff_json before/after 视觉对照 (Task 12 红/绿色) ──
    await expect(
      dialog.getByText('- {"status":"proposed"}'),
    ).toBeVisible();
    await expect(
      dialog.getByText('+ {"status":"canonical"}'),
    ).toBeVisible();
  });

  // ════════ 2. batch progress visible during bulk approve ════════
  test("batch progress visible during bulk approve", async ({ page }) => {
    await setupKnowledgePage(page);

    // 5 条 proposed
    const items = [
      makeProposed(200, "术语-1"),
      makeProposed(201, "术语-2"),
      makeProposed(202, "术语-3"),
      makeProposed(203, "术语-4"),
      makeProposed(204, "术语-5"),
    ];
    await page.route("**/api/knowledge/audit/queue*", (r) =>
      r.fulfill({
        json: { items, total: items.length, page: 1, size: 20 },
      }),
    );

    // batch 端点 — 加 600ms delay 让 BatchProgress 0/5 → 5/5 有可观察窗口
    await page.route("**/api/knowledge/audit/batch", async (r) => {
      await new Promise((res) => setTimeout(res, 600));
      return r.fulfill({
        json: {
          affected_count: 5,
          success_ids: items.map((i) => i.id),
        },
      });
    });

    await page.goto("/knowledge");

    // 选 namespace
    await page.locator(".ant-select").first().click();
    await page.getByText("测试空间").click();

    // 切待审 tab
    await page.getByRole("button", { name: /待审/ }).click();

    // 5 张卡渲染
    for (const it of items) {
      await expect(page.getByText(it.content)).toBeVisible();
    }

    // 全选: antd Checkbox hidden input 用 force click
    const checkboxes = page.locator(".ant-checkbox-input");
    const count = await checkboxes.count();
    expect(count).toBeGreaterThanOrEqual(items.length);
    for (let i = 0; i < items.length; i++) {
      await checkboxes.nth(i).check({ force: true });
    }

    // BatchAuditBar 出现 "已选 5 条"
    await expect(page.getByText("已选 5 条")).toBeVisible();

    // 点批量通过 — 触发 setProgress({total:5, done:0}) → 600ms 后 done:5
    await page.getByRole("button", { name: "批量通过" }).click();

    // 0/5 在 600ms 延迟窗口内可见 (BatchProgress 渲染锚点)
    await expect(page.getByText("0/5")).toBeVisible({ timeout: 1000 });
    // antd Progress 进度条 DOM 出现
    await expect(page.locator(".ant-progress").first()).toBeVisible();

    // batch API 完成后: BatchAuditBar 因 selected.clear() 立即 unmount,
    // "5/5" 仅渲染一帧不可靠. 改 assert success toast (notification.success)
    // 内容 "已批量通过 5 条" — 这是 batch 成功的最终业务证据.
    await expect(page.getByText(/已批量.*5\s*条/)).toBeVisible({ timeout: 3000 });
  });
});
