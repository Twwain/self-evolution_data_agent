/* ════════════════════════════════════════════════════════════════════════════
 *  query-page 视觉回归 — 仅保留登录态前的页面级快照
 *  Stage 6 重构后, 旧 emptyState/sendQuery 同步路径的断言已迁移到
 *  query_stream.spec.ts (mock SSE 完整链路).
 * ══════════════════════════════════════════════════════════════════════════ */

import { test, expect } from '@playwright/test';

test.describe('视觉回归测试 @visual', () => {
  test('完整页面快照', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // 隐藏动态时间戳
    await page.evaluate(() => {
      document.querySelectorAll('[data-dynamic], .timestamp, time').forEach(el => {
        (el as HTMLElement).style.visibility = 'hidden';
      });
    });

    await expect(page).toHaveScreenshot('query-page-full.png', {
      fullPage: true,
      maxDiffPixels: 200,
    });
  });
});
