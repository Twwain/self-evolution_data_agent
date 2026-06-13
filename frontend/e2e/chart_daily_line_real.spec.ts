/* ════════════════════════════════════════════════════════════════════════════
 *  Stage 7 — 真端到端验收门 (终, design.md §8)
 * ----------------------------------------------------------------------------
 *  范式 (硬性): 真 Playwright, 零 mock —— 真登录 + 真后端 agent loop + 真 LLM
 *  + 真查 new_energy namespace D/G 数据, 复现原始案例原句, 强断言锁死两个原症状.
 *
 *  环境前提 (memory 实证, 执行前必须就绪):
 *   - 真后端 `make dev-backend` (禁 --reload, 本地 8001) + 真前端 (vite 3000)
 *   - new_energy namespace 数据源背后有 electricity_generation_monitor 的 D/G 数据
 *   - admin 真登录凭据可用 (fake-jwt 会被 401 弹回 /login)
 *   - LLM provider 配置就绪 (IS_LLM_*)
 *
 *  产品化红线: D/G 站名 / new_energy / 表名经 env 注入, 不硬编码进可开源断言常量;
 *  断言用结构性条件 (series.length===2 / x 去重 / 点数===期望日期数).
 *
 *  人工触发, 不进默认 CI. 缺 E2E_ADMIN_PASS 时整组 skip.
 * ══════════════════════════════════════════════════════════════════════════ */

import { test, expect } from "@playwright/test";

// 真环境凭据/数据从 env 注入, 不硬编码 (产品化红线)
const ADMIN_USER = process.env.E2E_ADMIN_USER || "admin";
const ADMIN_PASS = process.env.E2E_ADMIN_PASS || "";
const NS_NAME = process.env.E2E_NS_NAME || "new_energy";
const NS_ID = Number(process.env.E2E_NS_ID || "0");
// 原始案例原句 (验收输入) — 仅在真环境出现, 不入可开源断言常量
const QUESTION =
  process.env.E2E_QUESTION ||
  "D电站和G电站在2024-2025年度发电量的对比，需要输出D电站和G电站2024年1月1日到2025年12月31日的实发电量的总和，并做出以日时间维度的折线图";

async function realLogin(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByPlaceholder("username").fill(ADMIN_USER);
  await page.getByPlaceholder("password").fill(ADMIN_PASS);
  await page.getByRole("button", { name: /登录|登 录|login/i }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 10000 });
}

// 完整真实流程: 真登录 → 真选 ns → 真输入原句 → 捕获真实 SSE final_answer (零 mock)
async function runRealQuery(page: import("@playwright/test").Page): Promise<any> {
  let finalAnswer: any = null;
  page.on("response", async (resp) => {
    if (resp.url().includes("/api/query/stream") && !finalAnswer) {
      try {
        const text = await resp.text();
        // SSE data 为单行 JSON; 用非贪婪 \{.*?\} 会被嵌套对象截断, 故按行取 data: 后整行.
        for (const block of text.split("\n\n")) {
          if (!block.includes("event: final_answer")) continue;
          for (const line of block.split("\n")) {
            if (line.startsWith("data:")) {
              finalAnswer = JSON.parse(line.slice(5).trim());
              break;
            }
          }
        }
      } catch {
        /* 流式不可 .text() 时走 history 兜底 */
      }
    }
  });

  await realLogin(page);
  await page.locator(".ant-select").first().click();
  await page.getByText(NS_NAME, { exact: false }).first().click();

  const input = page.getByPlaceholder(/输入统计需求/);
  await input.fill(QUESTION);
  await input.press("Enter");

  await expect(page.getByText("最终结果")).toBeVisible({ timeout: 180_000 });
  await expect(page.locator("canvas").first()).toBeVisible();

  // 流式响应无法 .text() 时, 从 history 真接口兜底取快照 (Stage 3 已落 truncated)
  if (!finalAnswer) {
    const list = await page.evaluate(async (nsId) => {
      const r = await fetch(`/api/namespaces/${nsId}/history`, {
        headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
      });
      return r.json();
    }, NS_ID);
    const rec = Array.isArray(list) ? list[0] : null;
    if (rec?.result_snapshot) finalAnswer = JSON.parse(rec.result_snapshot);
  }
  expect(finalAnswer, "未捕获真实 final_answer").toBeTruthy();
  return finalAnswer;
}

test.describe("真端到端: D/G 按日发电量折线 (终验收门)", () => {
  test.skip(!ADMIN_PASS, "需 E2E_ADMIN_PASS + 真环境 (new_energy namespace 数据可达)");
  test.setTimeout(200_000);

  test("原始案例复现: 按日折线, D/G 两条线, 全天数", async ({ page }) => {
    const finalAnswer = await runRealQuery(page);

    expect(finalAnswer.chart_type).toBe("line");
    const series = finalAnswer.chart_option?.series || [];
    expect(series.length).toBe(2); // D/G 两条线 (锁锯齿单线/根因1)
    const xData = finalAnswer.chart_option?.xAxis?.data || [];
    expect(new Set(xData).size).toBe(xData.length); // x 去重 (锁一天两点)
    const sorted = [...xData].sort();
    expect(xData).toEqual(sorted); // x 按日升序
    expect(finalAnswer.truncated).toBe(false); // 未截断才谈"全天数"
    // x 点数 === 期望 distinct 日期数 (取代弱 >5; 经独立 count 取期望, 截断则红, 锁根因2)
    const expectedPoints = Number(process.env.E2E_EXPECTED_DATE_POINTS || "0");
    expect(
      expectedPoints,
      "必须先用独立 count 查 D/G 区间 distinct 日期数, 经 E2E_EXPECTED_DATE_POINTS 注入; 不接受弱 >5",
    ).toBeGreaterThan(0);
    expect(xData.length).toBe(expectedPoints);
  });
});

test.describe("真端到端 §8.5: 截断显式 + agent 降级", () => {
  test.skip(!ADMIN_PASS, "需真环境 + 后端 export IS_RENDER_ROW_LIMIT=500");
  test.setTimeout(200_000);

  test("验收点 A — 截断全程显式 (truncated 标志 + 总数/展示数 + banner)", async ({ page }) => {
    const finalAnswer = await runRealQuery(page);
    // 走 (b) 直接呈现路径时 truncated=true; 走 (a) 降级时本断言可能为 false (见验收点 B)
    if (finalAnswer.truncated === true) {
      expect(finalAnswer.rendered_row_count).toBe(500);
      expect(finalAnswer.total_row_count).toBeGreaterThan(500); // 补 count 拿到的精确总数
      await expect(page.getByText("结果已截断")).toBeVisible();
      await expect(page.getByText(/结果共 \d+ 行/)).toBeVisible();
      await expect(page.getByText(/仅展示前 500 行/)).toBeVisible();
    }
  });

  test("验收点 B — 截断后 agent 粗化降级 或 显式截断 (二者其一, 静默必红)", async ({ page }) => {
    const finalAnswer = await runRealQuery(page);
    const xData = finalAnswer.chart_option?.xAxis?.data || [];
    // (a) 降级: 粒度变粗且未截断 (点数明显 <1462, 如按月 ≈24)
    const coarsened = finalAnswer.truncated === false && xData.length < 1462 && xData.length > 0;
    // (b) 直接呈现: 截断标志为真 (验收点 A 已验 banner 显式)
    const explicitTruncated = finalAnswer.truncated === true;
    // 二者其一即绿; 都不成立 (= 静默吐截断图/无标志) 必红
    expect(coarsened || explicitTruncated).toBe(true);
  });
});
