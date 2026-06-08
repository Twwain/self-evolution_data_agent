import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/__tests__/setup.ts"],
    globals: true,
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "json-summary"],
      include: ["src/**/*.{ts,tsx}"],
      // ── Stage 6 范围: stream/* + audit/* + sse client + agent stream hook + sse types
      // 旧 (非 Stage 6) 文件临时排除, 不在 Stage 6 验收覆盖率门内.
      exclude: [
        "src/**/*.d.ts",
        "src/main.tsx",
        "src/__tests__/**",
        "src/vite-env.d.ts",
        "src/App.tsx",
        "src/theme.ts",
        "src/api/index.ts",
        "src/components/ChartRenderer.tsx",
        "src/components/ChatInput.tsx",
        "src/components/ClarifyQuestionCard.tsx",
        "src/components/Layout.tsx",
        "src/components/NamespaceSelector.tsx",
        "src/components/RepoManager.tsx",
        "src/components/ResultDisplay.tsx",
        "src/components/DataTable.tsx",
        "src/context/**",
        "src/pages/**",
        "src/types/index.ts",
      ],
      thresholds: { lines: 80, functions: 80, branches: 75, statements: 80 },
    },
  },
});
