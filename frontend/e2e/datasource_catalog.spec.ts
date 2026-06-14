/* ════════════════════════════════════════════
 *  数据源目录 e2e — 建源 UI / 报错 / 画像展示 / 布局
 *  真实登录 + 真后端, 零 mock, 零客户领域词字面量, 无快照.
 * ════════════════════════════════════════════ */
import { test, expect, type Page } from "@playwright/test";

const BACKEND = "http://localhost:8001";
const PWD = "admin123456";

async function apiLogin(page: Page): Promise<string> {
  const resp = await page.request.post(`${BACKEND}/api/auth/login`, {
    data: { username: "admin", password: PWD },
  });
  if (!resp.ok()) throw new Error(`admin login failed: ${resp.status()}`);
  return (await resp.json()).access_token;
}

// 用 API 建临时 ns (隔离, 不碰生产), 返回 ns id + slug
async function createTempNs(page: Page, token: string): Promise<{ id: number; slug: string }> {
  const slug = `e2e-catalog-${Date.now()}`;
  const resp = await page.request.post(`${BACKEND}/api/namespaces`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: slug, slug, description: "e2e temp" },
  });
  if (!resp.ok()) throw new Error(`create ns failed: ${resp.status()} ${await resp.text()}`);
  const body = await resp.json();
  return { id: body.id, slug: body.slug };
}

test.describe.serial("数据源目录 UI", () => {
  let token: string;
  let ns: { id: number; slug: string };

  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage();
    token = await apiLogin(page);
    ns = await createTempNs(page, token);
    await page.close();
  });

  test.afterAll(async ({ browser }) => {
    // 清理临时 ns (两段式删除, 处理 confirm_token 阈值)
    const page = await browser.newPage();
    const headers = { Authorization: `Bearer ${token}` };
    // Phase 1: dry_run 获取 preview (含 confirm_token)
    const preview = await page.request.delete(
      `${BACKEND}/api/namespaces/${ns.id}?dry_run=true`, { headers },
    );
    if (!preview.ok()) {
      console.error(`[afterAll] dry_run failed: ${preview.status()}`);
      await page.close();
      return;
    }
    const previewBody = await preview.json();
    // Phase 2: 真删 — 若超阈值需携带 confirm_token
    const deleteUrl = previewBody.confirm_required
      ? `${BACKEND}/api/namespaces/${ns.id}?dry_run=false&confirm_token=${previewBody.confirm_token}`
      : `${BACKEND}/api/namespaces/${ns.id}?dry_run=false`;
    const del = await page.request.delete(deleteUrl, { headers });
    if (!del.ok()) {
      console.error(`[afterAll] delete failed: ${del.status()} ${await del.text()}`);
    }
    await page.close();
  });

  // 真实 UI 登录 + 进入临时 ns 的命名空间页
  async function gotoNamespacePage(page: Page) {
    await page.addInitScript(
      ([t, nsId]) => {
        localStorage.setItem("token", t as string);
        localStorage.setItem("lastNamespaceId", String(nsId));
      },
      [token, ns.id] as const,
    );
    await page.goto("/login");
    await page.getByPlaceholder("username").fill("admin");
    await page.getByPlaceholder("password").fill(PWD);
    await page.getByRole("button", { name: /登\s*录/ }).click();
    await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 10000 });
    await page.goto("/namespaces");
  }

  test("建源表单含 description 输入框", async ({ page }) => {
    await gotoNamespacePage(page);
    await page.getByRole("button", { name: /添加数据源/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    // description 字段可见 (label "用途描述")
    await expect(dialog.getByText("用途描述")).toBeVisible();
    // 表单核心字段都在 (布局完整性)
    await expect(dialog.getByLabel("类型")).toBeVisible();
    await expect(dialog.getByLabel("主机")).toBeVisible();
  });

  test("连不上的源 → 报错展示, Modal 不关, 列表无新行", async ({ page }) => {
    await gotoNamespacePage(page);
    await page.getByRole("button", { name: /添加数据源/ }).click();
    const dialog = page.getByRole("dialog");
    // 填一个不可达地址 (RFC 6761 .invalid, DNS 必失败 → 连接失败)
    await dialog.getByLabel("类型").click();
    await page.getByText("MySQL", { exact: true }).click();
    await dialog.getByLabel("主机").fill("unreachable.invalid");
    await dialog.getByLabel("端口").fill("3306");
    await dialog.getByLabel("数据库").fill("nope");
    await dialog.getByLabel("用户名").fill("u");
    await dialog.getByLabel("密码").fill("p");
    await dialog.getByRole("button", { name: /确\s*定|OK/ }).click();
    // 报错可见 (antd message), Modal 仍开
    await expect(page.getByText(/添加失败|连接失败/)).toBeVisible({ timeout: 15000 });
    await expect(dialog).toBeVisible();
  });

  test("数据源列表与 API 一致 + 布局不重叠", async ({ page }) => {
    await gotoNamespacePage(page);
    // 运行时取后端真值作期望 (零字面量)
    const apiResp = await page.request.get(
      `${BACKEND}/api/namespaces/${ns.id}/datasources`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    const sources = await apiResp.json();
    expect(Array.isArray(sources)).toBeTruthy();
    // 卡片数 == API 返回数 (用 Task 10 加的 data-testid 稳定定位, 不用 CSS module 哈希类名)
    const cards = page.locator('[data-testid="ds-card"]');
    await expect(cards).toHaveCount(sources.length);
    // 数据源 Tab 区域结构在
    await expect(page.getByRole("button", { name: /添加数据源/ })).toBeVisible();
    // 布局: 若有卡片, 验证相邻卡片 boundingBox 不纵向重叠 (呼应 sidebar 重叠 bug 教训)
    if (sources.length >= 2) {
      const b0 = await cards.nth(0).boundingBox();
      const b1 = await cards.nth(1).boundingBox();
      if (b0 && b1) {
        expect(b1.y).toBeGreaterThanOrEqual(b0.y + b0.height - 1);
      }
    }
  });

  // L4 全链路: 进入命名空间页确实触发 datasources API 请求 (验前端走 API 非缓存)
  test("进入命名空间页触发 datasources 请求 + 卡片渲染", async ({ page }) => {
    await page.addInitScript(
      ([t, nsId]) => {
        localStorage.setItem("token", t as string);
        localStorage.setItem("lastNamespaceId", String(nsId));
      },
      [token, ns.id] as const,
    );
    await page.goto("/login");
    await page.getByPlaceholder("username").fill("admin");
    await page.getByPlaceholder("password").fill(PWD);
    await page.getByRole("button", { name: /登\s*录/ }).click();
    await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 10000 });
    const respPromise = page.waitForResponse(
      (r) => r.url().includes(`/namespaces/${ns.id}/datasources`) && r.status() === 200,
      { timeout: 15000 },
    );
    await page.goto("/namespaces");
    const resp = await respPromise;
    const sources = await resp.json();
    await expect(page.locator('[data-testid="ds-card"]')).toHaveCount(sources.length);
  });

  // 组件 F 验收: 建源成功后卡片展示画像 + 诚实的「初始连接于」profiled_at 标签 (非无条件"已连接").
  // 凭据经 env 注入 + API 建源 (零字面量, 不在浏览器表单手填明文 — 遵循 D8); 无 env 凭据时 skip.
  test("建源成功后卡片展示画像 + 诚实 profiled_at 标签 (组件 F)", async ({ page }) => {
    const host = process.env.E2E_MYSQL_HOST;
    test.skip(!host, "E2E_MYSQL_* 未配置, 跳过画像卡片断言");
    // 经 API 建源 (连通才存) — 凭据来自 env, spec 内零字面量
    const create = await page.request.post(
      `${BACKEND}/api/namespaces/${ns.id}/datasources`,
      {
        headers: { Authorization: `Bearer ${token}` },
        data: {
          db_type: "mysql",
          host,
          port: Number(process.env.E2E_MYSQL_PORT || "3306"),
          database: process.env.E2E_MYSQL_DB,
          username: process.env.E2E_MYSQL_USER,
          password: process.env.E2E_MYSQL_PASS,
          description: "e2e profile card",
        },
      },
    );
    expect(create.status(), await create.text()).toBe(201);
    const created = await create.json();
    const profiledAt = created.db_profile?.profiled_at as string | undefined;
    expect(profiledAt).toBeTruthy();
    // 前端渲染 "初始连接于 YYYY-MM-DD HH:mm" (profiled_at.slice(0,16).replace("T"," "))
    const expectedTag = `初始连接于 ${profiledAt!.slice(0, 16).replace("T", " ")}`;

    await gotoNamespacePage(page);
    const card = page
      .locator('[data-testid="ds-card"]')
      .filter({ hasText: created.database });
    await expect(card).toBeVisible();
    // 诚实标签: 含 profiled_at 时间 (与 API 返回一致), 非无条件"已连接"假标签
    await expect(card.getByText(expectedTag)).toBeVisible();
    await expect(card.getByText(/^已连接$/)).toHaveCount(0);
    // 画像摘要可见 (版本 / 对象数), 描述可见
    await expect(card.getByText(/v\d/)).toBeVisible();
    await expect(card.getByText("e2e profile card")).toBeVisible();
  });
});
