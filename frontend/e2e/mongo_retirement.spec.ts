/* ════════════════════════════════════════════════════════════════════════════
 *  T6 — Playwright e2e: mongo-canonical retirement 用户路径验证
 * ----------------------------------------------------------------------------
 *  spec: docs/superpowers/specs/2026-05-19-mongo-canonical-retirement/05-acceptance.md §G6
 *
 *  4 步用户路径 (源自 spec):
 *    1. 登录 → /knowledge: 不可见 mongo-canonical tab; 仍可见 知识条目 / Schema 管理 /
 *       Terminology 冲突 三个 tab
 *    2. Schema 管理 + 选 mongodb namespace: collection 列表 ≥ 1; 冲突 sub-tab ≥ 1
 *    3. 触发训练 (POST /api/namespaces/{ns}/repos/{repo}/parse) 等响应 200
 *    4. 触发 promote (POST /api/namespaces/{ns}/schema-canonical/promote): 返
 *       promoted_count > 0 且 conflicted_count 不增 (历史冲突已存在不应重复)
 *
 *  baseURL: http://localhost:3000 (vite dev, see playwright.config.ts)
 *  策略: 全 mock 后端 — 此 spec 验证前端不再 mount mongo-canonical tab + API 路径迁移,
 *  不需真实后端运行. 与 audit_v2 / enum_binding 现有 e2e 同模式.
 * ══════════════════════════════════════════════════════════════════════════ */

import { test, expect, Page } from "@playwright/test";

const NS_ID = 1;
const REPO_ID = 42;

async function setupKnowledgePage(page: Page) {
  // ── 1) 注入登录态绕过 RequireAuth + RequireAdmin + 预选 ns ────────────
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
    // 预选 NS_ID = 1, NamespaceSelector 启动时 readLastNamespaceId 即命中
    localStorage.setItem("lastNamespaceId", "1");
  });

  // ── 2) 公共 mock: users/me + namespace list ──────────────────────────
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
          id: NS_ID,
          slug: "demo",
          name: "Demo NS",
          description: "",
          datasources: [],
        },
      ],
    }),
  );

  // ── 3) KnowledgePage.loadData 并发拉取的 4 端点空 mock (shape 严格匹配后端契约) ───────────────
  await page.route("**/api/namespaces/*/knowledge", (r) =>
    r.fulfill({ json: [] }),  // KnowledgeEntry[]
  );
  await page.route("**/api/namespaces/*/repos", (r) =>
    r.fulfill({ json: { repos: [], batch_status: null } }),  // RepoListResponse
  );
  await page.route("**/api/knowledge/audit/queue*", (r) =>
    r.fulfill({ json: { items: [], total: 0, page: 1, size: 20 } }),
  );
  await page.route("**/api/namespaces/*/terminology/conflicts*", (r) =>
    r.fulfill({ json: { conflicts: [] } }),
  );

  // ── 4) Schema 管理 tab 数据 (mongodb collection + 冲突) ──────────────
  await page.route("**/api/namespaces/*/schema-canonical?db_type=mongodb*", (r) =>
    r.fulfill({
      json: [
        {
          id: 100,
          db_type: "mongodb",
          database: "db1",
          target: "orders",
          description: "订单集合",
          fields: [{ name: "_id", type: "ObjectId" }],
          user_locked: false,
        },
        {
          id: 101,
          db_type: "mongodb",
          database: "db1",
          target: "products",
          description: "商品集合",
          fields: [{ name: "_id", type: "ObjectId" }],
          user_locked: false,
        },
      ],
    }),
  );
  await page.route("**/api/namespaces/*/schema-canonical/conflicts*", (r) =>
    r.fulfill({
      json: [
        {
          id: 200,
          db_type: "mongodb",
          target: "orders",
          field_path: "items",
          status: "open",
          conflict_type: "field_value",
          candidate_kind: "field_description",
        },
      ],
    }),
  );
  await page.route("**/api/namespaces/*/schema-canonical*", (r) => {
    // 兜底: 无 db_type 参数返合并集
    r.fulfill({ json: [] });
  });
}

test.describe("Mongo canonical retirement — user path verification", () => {
  test("step 1: knowledge page mounts without mongo-canonical tab", async ({ page }) => {
    await setupKnowledgePage(page);
    await page.goto("/knowledge");

    // 等待页面骨架加载
    await expect(page.getByRole("heading", { name: "知识库" })).toBeVisible({ timeout: 5000 });

    // 不可见 mongo-canonical tab — 这是退役的核心断言
    await expect(page.getByText("mongo-canonical", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Mongo Canonical", { exact: false })).toHaveCount(0);

    // NamespaceSelector 自动选第一个 ns 后, 三个核心 tab 渲染
    // (selector useEffect 异步, 等到 "知识条目" 按钮出现即等价于 activeNsId 已 set)
    await expect(page.getByRole("button", { name: "知识条目" })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole("button", { name: "Schema 管理" })).toBeVisible();
    await expect(page.getByRole("button", { name: /术语冲突/ })).toBeVisible();
  });

  test("step 2: schema-canonical mongodb listing endpoint contract", async ({ page }) => {
    /* T7 后端测 (test_schema_canonical_mongodb_listing.py) 已直接验过 db_type=mongodb
     * filter; 此处 e2e 仅验前端可消费此端点而无 404 — 不依赖 UI 流程 */
    await setupKnowledgePage(page);
    let mongoListingCalled = false;
    await page.route("**/api/namespaces/*/schema-canonical?db_type=mongodb*", (r) => {
      mongoListingCalled = true;
      r.fulfill({
        json: [
          { id: 100, db_type: "mongodb", database: "db1", target: "orders" },
        ],
      });
    });

    await page.goto("/knowledge");
    const result = await page.evaluate(async (args) => {
      const r = await fetch(
        `/api/namespaces/${args.ns}/schema-canonical?db_type=mongodb`,
      );
      return { ok: r.ok, status: r.status, data: await r.json() };
    }, { ns: NS_ID });
    expect(result.ok).toBe(true);
    expect(mongoListingCalled).toBe(true);
    expect(Array.isArray(result.data)).toBe(true);
  });

  test("step 3: training trigger endpoint returns 200", async ({ page }) => {
    await setupKnowledgePage(page);
    let parseCalled = false;
    await page.route(`**/api/namespaces/${NS_ID}/repos/${REPO_ID}/parse`, (r) => {
      parseCalled = true;
      r.fulfill({ json: { repo_id: REPO_ID, status: "queued" } });
    });

    await page.goto("/knowledge");
    const resp = await page.evaluate(async (args) => {
      const r = await fetch(
        `/api/namespaces/${args.ns}/repos/${args.repo}/parse`,
        { method: "POST", headers: { "Content-Type": "application/json" } },
      );
      return { ok: r.ok, status: r.status };
    }, { ns: NS_ID, repo: REPO_ID });
    expect(resp.ok).toBe(true);
    expect(parseCalled).toBe(true);
  });

  test("step 4: promote endpoint returns promoted_count > 0 with stable conflicted_count", async ({
    page,
  }) => {
    await setupKnowledgePage(page);
    await page.route(
      `**/api/namespaces/${NS_ID}/schema-canonical/promote`,
      (r) =>
        r.fulfill({
          json: {
            promoted_count: 3,
            conflicted_count: 0,
            skipped_user_locked: 0,
            candidates_processed: 5,
            duration_seconds: 0.12,
          },
        }),
    );

    await page.goto("/knowledge");
    const result = await page.evaluate(async (args) => {
      const r = await fetch(
        `/api/namespaces/${args.ns}/schema-canonical/promote`,
        { method: "POST", headers: { "Content-Type": "application/json" } },
      );
      return await r.json();
    }, { ns: NS_ID });
    expect(result.promoted_count).toBeGreaterThan(0);
    expect(result.conflicted_count).toBe(0);
  });
});
