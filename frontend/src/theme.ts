/* ════════════════════════════════════════════
 *  Ant Design 主题 Token — 温暖蓝调
 * ════════════════════════════════════════════ */

import type { ThemeConfig } from "antd";

const theme: ThemeConfig = {
  token: {
    colorPrimary: "#2563eb",
    colorBgContainer: "#ffffff",
    colorBorder: "#e0e7ff",
    colorText: "#1e3a5f",
    colorTextSecondary: "#64748b",
    colorSuccess: "#10b981",
    colorWarning: "#f59e0b",
    colorError: "#ef4444",
    borderRadius: 10,
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif",
  },
  components: {
    Button: { borderRadius: 6 },
    Input: { borderRadius: 12 },
    Modal: { borderRadiusLG: 12 },
    Card: { borderRadiusLG: 10 },
    Select: { borderRadius: 8 },
  },
};

export default theme;
