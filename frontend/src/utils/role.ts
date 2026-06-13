/* ════════════════════════════════════════════
 *  角色层级 helper — 与后端 ROLE_LEVEL 对齐
 * ════════════════════════════════════════════ */
import type { Role } from "@/types";

const LEVEL: Record<Role, number> = { user: 0, admin: 1, super_admin: 2 };

export const roleAtLeast = (r: Role | undefined, min: Role): boolean =>
  (r ? LEVEL[r] : -1) >= LEVEL[min];
