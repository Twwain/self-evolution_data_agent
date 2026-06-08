/* ════════════════════════════════════════════
 *  useLastNamespaceId — 跨页命名空间选择记忆
 * ----------------------------------------------------------------------------
 *  localStorage.lastNamespaceId 单一真相源, 任何页面切换命名空间后更新,
 *  其他页面进入时优先恢复; 找不到对应 ns 时调用方自行 fallback 到 list[0].
 * ════════════════════════════════════════════ */

const STORAGE_KEY = "lastNamespaceId";

export function readLastNamespaceId(): number | undefined {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return undefined;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function writeLastNamespaceId(id: number): void {
  localStorage.setItem(STORAGE_KEY, String(id));
}

export function clearLastNamespaceId(): void {
  localStorage.removeItem(STORAGE_KEY);
}
