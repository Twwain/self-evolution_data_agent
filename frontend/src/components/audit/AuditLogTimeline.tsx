/* ════════════════════════════════════════════
 *  AuditLogTimeline — 审计动作时间线
 * ════════════════════════════════════════════ */

import React, { useEffect, useState } from "react";
import { Empty, Spin, Tag, Timeline } from "antd";
import { fetchAuditLog, type AuditLogEntry } from "@/api";

const ACTION_COLORS: Record<string, string> = {
  approve: "green", reject: "red", edit: "blue",
  supersede: "purple", restore: "cyan", expire: "orange",
  hard_delete: "magenta",
};

export default function AuditLogTimeline({ entryId }: { entryId: number }) {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetchAuditLog(entryId)
      .then(setLogs)
      .finally(() => setLoading(false));
  }, [entryId]);

  if (loading) return <Spin />;
  if (!logs.length) return <Empty description="无审计记录" />;

  const renderDiff = (diff_json?: string | null) => {
    if (!diff_json) return null;
    try {
      const diff = JSON.parse(diff_json);
      if (diff?.before === undefined && diff?.after === undefined) return null;
      return (
        <div style={{ marginTop: 4, fontSize: 12 }}>
          <div style={{ color: "#ff4d4f" }}>- {JSON.stringify(diff.before)}</div>
          <div style={{ color: "#52c41a" }}>+ {JSON.stringify(diff.after)}</div>
        </div>
      );
    } catch { return null; }
  };

  return (
    <Timeline
      items={logs.map((log) => ({
        color: ACTION_COLORS[log.action] ?? "gray",
        children: (
          <div>
            <div>
              <Tag color={ACTION_COLORS[log.action] ?? "default"}>{log.action}</Tag>
              {log.from_status && <span>{log.from_status} → </span>}
              <span>{log.to_status}</span>
            </div>
            {log.reason && <div style={{ color: "#666" }}>{log.reason}</div>}
            {renderDiff((log as any).diff_json)}
            <div style={{ color: "#999", fontSize: 12 }}>
              {new Date(log.created_at).toLocaleString()}
              {log.actor_id != null ? ` · 操作员 #${log.actor_id}` : " · 系统"}
            </div>
          </div>
        ),
      }))}
    />
  );
}
