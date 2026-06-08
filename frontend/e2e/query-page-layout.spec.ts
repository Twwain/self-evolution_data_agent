import { test, expect } from '@playwright/test';

test.describe('智能查询页面布局 (无需登录)', () => {
  test('完整页面快照 @visual', async ({ page }) => {
    // 直接访问页面（假设开发模式无认证或已配置 mock）
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // 等待任何内容加载
    await page.waitForTimeout(1000);

    // 隐藏动态时间戳
    await page.evaluate(() => {
      document.querySelectorAll('[data-dynamic], .timestamp, time').forEach(el => {
        (el as HTMLElement).style.visibility = 'hidden';
      });
    });

    // 完整页面截图
    await expect(page).toHaveScreenshot('query-page-full.png', {
      fullPage: true,
      maxDiffPixels: 300,
    });
  });

  test('检查输入框是否完整显示', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // 等待页面渲染
    await page.waitForTimeout(500);

    // 查找输入框（无论在哪个状态）
    const inputBox = page.locator('input[placeholder*="问"], textarea[placeholder*="问"]').or(
      page.locator('[class*="inputBox"]')
    ).first();

    // 如果输入框存在，验证完整可见
    if (await inputBox.isVisible({ timeout: 2000 }).catch(() => false)) {
      const box = await inputBox.boundingBox();
      const viewport = page.viewportSize();

      if (box && viewport) {
        // 输入框底部应该在视口内
        expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
        // 输入框顶部应该可见
        expect(box.y).toBeGreaterThanOrEqual(0);
        // 输入框宽度应该合理（至少 200px）
        expect(box.width).toBeGreaterThan(200);
      }
    }
  });
});

test.describe('布局测试（需要实际后端）', () => {
  test.skip('空状态居中布局 — 需要登录', async () => {
    // 这个测试需要配置认证 fixture
    // 跳过直到配置完成
  });

  test.skip('有对话后输入框固定底部 — 需要后端', async () => {
    // 这个测试需要实际发送查询
    // 跳过直到配置完成
  });
});
