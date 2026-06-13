/* ════════════════════════════════════════════
 *  RBAC e2e 共享辅助 — 真实登录 (零 mock)
 * ════════════════════════════════════════════ */
import type { Page, APIRequestContext } from "@playwright/test";

export const PWD = "admin123456";

export async function login(page: Page, username: string, password = PWD) {
  await page.goto("/login");
  await page.getByPlaceholder("username").fill(username);
  await page.getByPlaceholder("password").fill(password);
  // antd 两字按钮 accessible name 含空格: "登 录"
  await page.getByRole("button", { name: /登\s*录/ }).click();
  await page.waitForURL((url) => !url.pathname.includes("/login"), { timeout: 10000 });
}

export async function apiToken(
  request: APIRequestContext, username: string, password = PWD,
): Promise<string> {
  const resp = await request.post("/api/auth/login", { data: { username, password } });
  if (!resp.ok()) throw new Error(`login failed: ${username} (${resp.status()})`);
  return (await resp.json()).access_token;
}
