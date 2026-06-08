/* ════════════════════════════════════════════
 *  App 路由入口 — 路由守卫 + 嵌套布局
 * ════════════════════════════════════════════ */

import React from "react";
import { Routes, Route, Navigate, Outlet } from "react-router-dom";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import Layout from "./components/Layout";
import QueryPage from "./pages/QueryPage";
import NamespacePage from "./pages/NamespacePage";
import KnowledgePage from "./pages/KnowledgePage";
import UserManagePage from "./pages/UserManagePage";
import ShareManagePage from "./pages/ShareManagePage";
import LoginPage from "./pages/LoginPage";
import ShareViewPage from "./pages/ShareViewPage";
import AgentTracesPage from "./pages/AgentTracesPage";

/* ── 认证守卫 ── */
const RequireAuth: React.FC = () => {
  const { token, loading } = useAuth();
  if (loading) return null; // 初始化中,避免闪烁
  return token ? <Outlet /> : <Navigate to="/login" replace />;
};

/* ── 管理员守卫 ── */
const RequireAdmin: React.FC = () => {
  const { user } = useAuth();
  return user?.role === "admin" ? <Outlet /> : <Navigate to="/" replace />;
};

const App: React.FC = () => (
  <AuthProvider>
    <Routes>
      {/* 公开路由 */}
      <Route path="/login" element={<LoginPage />} />
      <Route path="/share/:token" element={<ShareViewPage />} />

      {/* 认证后路由 — 嵌套 Layout */}
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route path="/" element={<QueryPage />} />

          {/* 管理员专属路由 */}
          <Route element={<RequireAdmin />}>
            <Route path="/namespaces" element={<NamespacePage />} />
            <Route path="/knowledge" element={<KnowledgePage />} />
            <Route path="/users" element={<UserManagePage />} />
            <Route path="/shares" element={<ShareManagePage />} />
            <Route path="/admin/agent-traces" element={<AgentTracesPage />} />
          </Route>
        </Route>
      </Route>
    </Routes>
  </AuthProvider>
);

export default App;
