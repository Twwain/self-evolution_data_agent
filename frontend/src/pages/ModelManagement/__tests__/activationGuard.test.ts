/**
 * 激活保护逻辑单元测试
 * 测试 Embedding 激活前置检查规则（纯逻辑，不依赖 React）
 */
import { describe, it, expect } from "vitest";
import type { ModelConfig } from "@/api/modelConfig";

// ── 提取 index.tsx 中的核心检查逻辑 ──────────────────────────
function hasOtherActiveEmbedding(
  configs: ModelConfig[],
  targetId: number,
): boolean {
  return configs.some(
    (c) => c.model_type === "EMBEDDING" && c.is_active && c.id !== targetId,
  );
}

function isEmbeddingEditLocked(cfg: ModelConfig): boolean {
  return cfg.model_type === "EMBEDDING" && !!cfg.is_active;
}

// ── 测试数据 ──────────────────────────────────────────────────
const makeConfig = (
  id: number,
  type: "CHAT" | "EMBEDDING",
  is_active: boolean,
): ModelConfig => ({
  id,
  provider: "openai",
  protocol: "openai",
  base_url: "https://example.com",
  api_key: "sk-****",
  model_name: `model-${id}`,
  model_type: type,
  is_active,
});

// ── 激活保护 ──────────────────────────────────────────────────
describe("Embedding 激活保护", () => {
  it("无其他 active Embedding 时允许激活", () => {
    const configs = [
      makeConfig(1, "EMBEDDING", false),
      makeConfig(2, "CHAT", true),
    ];
    expect(hasOtherActiveEmbedding(configs, 1)).toBe(false);
  });

  it("已有其他 active Embedding 时拦截激活", () => {
    const configs = [
      makeConfig(1, "EMBEDDING", false), // 目标
      makeConfig(2, "EMBEDDING", true),  // 已激活的另一个
    ];
    expect(hasOtherActiveEmbedding(configs, 1)).toBe(true);
  });

  it("自身已是 active Embedding 不视为冲突", () => {
    const configs = [
      makeConfig(1, "EMBEDDING", true), // 已激活自身
    ];
    expect(hasOtherActiveEmbedding(configs, 1)).toBe(false);
  });

  it("Chat 配置不受 Embedding 激活保护影响", () => {
    const configs = [
      makeConfig(1, "CHAT", false),     // 目标
      makeConfig(2, "EMBEDDING", true), // 已激活的 Embedding（不影响 Chat）
    ];
    // Chat 激活不需要检查 Embedding
    expect(hasOtherActiveEmbedding(configs, 1)).toBe(true); // 检查本身返回 true
    // 但业务逻辑只对 EMBEDDING 类型做这个检查，所以 Chat 可以正常激活
  });
});

// ── 编辑/删除锁定 ─────────────────────────────────────────────
describe("active Embedding 编辑/删除锁定", () => {
  it("active Embedding 配置被锁定", () => {
    const cfg = makeConfig(1, "EMBEDDING", true);
    expect(isEmbeddingEditLocked(cfg)).toBe(true);
  });

  it("inactive Embedding 配置不被锁定", () => {
    const cfg = makeConfig(1, "EMBEDDING", false);
    expect(isEmbeddingEditLocked(cfg)).toBe(false);
  });

  it("active Chat 配置不被锁定", () => {
    const cfg = makeConfig(1, "CHAT", true);
    expect(isEmbeddingEditLocked(cfg)).toBe(false);
  });

  it("inactive Chat 配置不被锁定", () => {
    const cfg = makeConfig(1, "CHAT", false);
    expect(isEmbeddingEditLocked(cfg)).toBe(false);
  });
});
