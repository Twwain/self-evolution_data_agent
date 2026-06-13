/* ════════════════════════════════════════════════════════════════════════════
 *  TerminologyConflict UI 闭环 e2e — 走真登录 + 真后端 seed 数据
 * ----------------------------------------------------------------------------
 *  前置: backend uvicorn 跑在 :8001, frontend vite dev 跑在 :3000.
 *  seed: backend/scripts/seed_terminology_conflict.py --json
 *    输出 { namespace_id, namespace_slug, canonical_entry_id, conflict_id }
 *
 *  4 case 覆盖 5 选项 (keep_existing / replace / merge_both / reject_both /
 *  manual_edit) 闭环 — 每 case 重新 seed 保证独立性.
 *
 *  Why no mock: fake JWT 触发 axios 401 拦截器自动登出 (api/index.ts), 整页弹回
 *  /login. 此处真后端 + admin/admin123456 真登录, e2e 走完整用户路径.
 * ══════════════════════════════════════════════════════════════════════════ */

import { test, expect, type Page } from "@playwright/test";
import { execSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";

interface SeedResult {
  namespace_id: number;
  namespace_slug: string;
  canonical_entry_id: number;
  conflict_id: number;
}

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const BACKEND_DIR = path.resolve(__dirname, "../../backend");

function seedConflict(): SeedResult {
  const out = execSync(
    "python scripts/seed_terminology_conflict.py --json",
    { cwd: BACKEND_DIR, encoding: "utf-8" },
  );
  return JSON.parse(out.trim()) as SeedResult;
}

async function loginAsAdmin(page: Page, nsId: number): Promise<string> {
  const resp = await page.request.post("http://localhost:8001/api/auth/login", {
    data: { username: "admin", password: "admin123456" },
  });
  if (!resp.ok()) {
    throw new Error(`admin login failed: ${resp.status()} ${await resp.text()}`);
  }
  const body = await resp.json();
  const token: string = body.access_token;
  const user = body.user;
  await page.addInitScript(
    ([t, u, ns]) => {
      localStorage.setItem("token", t as string);
      localStorage.setItem("user", JSON.stringify(u));
      localStorage.setItem("lastNamespaceId", String(ns));
    },
    [token, user, nsId] as const,
  );
  return token;
}

async function gotoConflictTab(page: Page): Promise<void> {
  await page.goto("/knowledge");
  // 等冲突 tab 标签出现 (count=1) 再点击
  const tabBtn = page.getByRole("button", { name: /术语冲突/ });
  await tabBtn.waitFor({ state: "visible", timeout: 10_000 });
  await tabBtn.click();
}

async function openModal(page: Page, conflictId: number): Promise<void> {
  await expect(page.getByText(new RegExp(`冲突 #${conflictId}`))).toBeVisible({
    timeout: 5000,
  });
  await page.getByRole("button", { name: /查看 \/ 解决/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
}

test.describe("terminology conflict UI — full closed loop (real backend)", () => {
  test.describe.configure({ mode: "serial" }); // seed 串行避免互相覆盖

  test("keep_existing — existing 不动, conflict 翻 resolved", async ({ page }) => {
    const seed = seedConflict();
    await loginAsAdmin(page, seed.namespace_id);
    await gotoConflictTab(page);
    await openModal(page, seed.conflict_id);

    await page.getByRole("dialog")
      .getByRole("button", { name: "choice-keep_existing" })
      .click();
    await expect(page.getByText(/已解决.*keep_existing/)).toBeVisible({ timeout: 3000 });
    await expect(page.getByRole("dialog")).not.toBeVisible({ timeout: 2000 });

    // 验证后端真翻成 resolved
    const verify = await page.request.get(
      `http://localhost:8001/api/namespaces/${seed.namespace_id}/terminology/conflicts?status=open`,
      { headers: { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem("token"))}` } },
    );
    const data = await verify.json();
    expect(data.conflicts.find((c: any) => c.id === seed.conflict_id)).toBeUndefined();
  });

  test("merge_both — existing.synonyms 扩充候选 term/synonyms", async ({ page }) => {
    const seed = seedConflict();
    await loginAsAdmin(page, seed.namespace_id);
    await gotoConflictTab(page);
    await openModal(page, seed.conflict_id);

    await page.getByRole("dialog")
      .getByRole("button", { name: "choice-merge_both" })
      .click();
    await expect(page.getByText(/已解决.*merge_both/)).toBeVisible({ timeout: 3000 });

    // 验证 KE.payload.synonyms ⊇ {货品(existing), 条目+明细(candidate term/synonyms 被合并)}
    const token = await page.evaluate(() => localStorage.getItem("token"));
    const ke = await page.request.get(
      `http://localhost:8001/api/namespaces/${seed.namespace_id}/knowledge`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    const list = await ke.json();
    const entry = list.find((e: any) => e.id === seed.canonical_entry_id);
    expect(entry).toBeDefined();
    const raw = entry.payload ?? entry.content;
    const payload = typeof raw === "string" ? JSON.parse(raw) : raw;
    const synSet = new Set<string>(payload.synonyms ?? []);
    for (const expected of ["货品", "条目", "明细"]) {
      expect(synSet.has(expected), `synonyms missing ${expected}`).toBe(true);
    }
  });

  test("replace — existing → superseded, 候选入 proposed", async ({ page }) => {
    const seed = seedConflict();
    await loginAsAdmin(page, seed.namespace_id);
    await gotoConflictTab(page);
    await openModal(page, seed.conflict_id);

    await page.getByRole("dialog")
      .getByRole("button", { name: "choice-replace" })
      .click();
    await expect(page.getByText(/已解决.*replace/)).toBeVisible({ timeout: 3000 });
  });

  test("reject_both — existing → rejected", async ({ page }) => {
    const seed = seedConflict();
    await loginAsAdmin(page, seed.namespace_id);
    await gotoConflictTab(page);
    await openModal(page, seed.conflict_id);

    await page.getByRole("dialog")
      .getByRole("button", { name: "choice-reject_both" })
      .click();
    await expect(page.getByText(/已解决.*reject_both/)).toBeVisible({ timeout: 3000 });
  });

  test("manual_edit — 展开内嵌表单, 保存翻 canonical", async ({ page }) => {
    const seed = seedConflict();
    await loginAsAdmin(page, seed.namespace_id);
    await gotoConflictTab(page);
    await openModal(page, seed.conflict_id);

    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "choice-manual_edit" }).click();

    // 内嵌表单出现 (路由锁定)
    await expect(dialog.locator('[data-testid="manual-edit-form"]')).toBeVisible();

    // 直接点保存 (用 initial merged synonyms)
    await dialog.getByRole("button", { name: "manual-edit-save" }).click();
    await expect(page.getByText(/已手动编辑并通过/)).toBeVisible({ timeout: 3000 });
  });
});
