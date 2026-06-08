/**
 * 归一化 rows 数据：支持 Record<string, unknown>[] 和 unknown[][] 两种格式，
 * 统一输出为二维数组 (unknown[][])，按 columns 顺序提取值。
 *
 * 所有消费 rows 的组件（ChartRenderer / DataTable / FinalResult）必须经过此函数，
 * 避免格式不一致导致渲染异常。
 */
export function normalizeRows(rows: unknown[], columns: string[]): unknown[][] {
  if (!rows.length) return [];
  if (Array.isArray(rows[0])) return rows as unknown[][];
  // dict 格式 → 按 columns 顺序转为二维数组
  return rows.map((r) => columns.map((c) => (r as Record<string, unknown>)[c]));
}
