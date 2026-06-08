/* ════════════════════════════════════════════
 *  分享查看页 — 公开访问, 无需登录
 *  通过 URL token 加载查询结果快照
 * ════════════════════════════════════════════ */

import React, { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Spin } from "antd";
import ResultDisplay from "@/components/ResultDisplay";
import { viewShare } from "@/api";
import type { ShareViewResponse } from "@/types";

const ShareViewPage: React.FC = () => {
  const { token } = useParams<{ token: string }>();
  const [data, setData] = useState<ShareViewResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) return;
    viewShare(token)
      .then(setData)
      .catch((e) => {
        const status = e?.response?.status;
        if (status === 404) setError("分享链接无效或已停用");
        else if (status === 410) setError("分享链接已过期");
        else setError("加载失败, 请稍后重试");
      })
      .finally(() => setLoading(false));
  }, [token]);

  if (loading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100vh" }}>
        <Spin size="large" tip="加载中..." />
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100vh", color: "#64748b" }}>
        <div style={{ fontSize: 48, marginBottom: 16 }}>:/</div>
        <div style={{ fontSize: 16 }}>{error}</div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div style={{ maxWidth: 960, margin: "0 auto", padding: "32px 16px" }}>
      {/* 头部信息 */}
      <div style={{ marginBottom: 24, paddingBottom: 16, borderBottom: "1px solid #e0e7ff" }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, color: "#1e3a5f", margin: 0 }}>
          查询结果分享
        </h1>
        <p style={{ fontSize: 12, color: "#94a3b8", marginTop: 6 }}>
          由 {data.shared_by_name} 分享于 {new Date(data.shared_at).toLocaleString("zh-CN")}
        </p>
      </div>

      {/* SQL / Query */}
      {data.result.generated_query && (
        <code style={{
          display: "block",
          background: "#f8fafc",
          border: "1px solid #e2e8f0",
          borderRadius: 6,
          padding: "8px 12px",
          fontSize: 11,
          color: "#475569",
          fontFamily: "'SF Mono', Monaco, Consolas, monospace",
          whiteSpace: "pre-wrap",
          marginBottom: 16,
        }}>
          {data.result.generated_query}
        </code>
      )}

      {/* 结果展示 — 复用 ResultDisplay */}
      <ResultDisplay result={data.result} />
    </div>
  );
};

export default ShareViewPage;
