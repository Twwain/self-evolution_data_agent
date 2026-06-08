import { Button, Select, Space, Tag, Timeline } from "antd";
import { useEffect, useState } from "react";

import { schemaCanonicalApi } from "@/api";
import type { SchemaAuditLogEntry } from "@/types/schema-canonical";

const SCHEMA_ACTIONS = [
  "auto_extract", "auto_promote", "auto_supersede",
  "conflict_open_diff", "conflict_open_semantic",
  "conflict_resolve_keep_a", "conflict_resolve_keep_b",
  "conflict_resolve_merge", "conflict_resolve_reject",
  "user_confirm", "user_correct", "user_ignore",
  "user_lock", "user_unlock",
  "field_enum_manual_bind", "field_enum_manual_unbind",
  "field_sample_collected",
  "skipped_user_locked",
];

const ACTION_COLORS: Record<string, string> = {
  auto_extract: "default",
  auto_promote: "green",
  auto_supersede: "default",
  conflict_open_diff: "orange",
  conflict_open_semantic: "gold",
  conflict_resolve_keep_a: "blue",
  conflict_resolve_keep_b: "blue",
  conflict_resolve_merge: "purple",
  conflict_resolve_reject: "red",
  user_confirm: "green",
  user_correct: "blue",
  user_ignore: "default",
  user_lock: "red",
  user_unlock: "cyan",
  field_enum_manual_bind: "blue",
  field_enum_manual_unbind: "orange",
  field_sample_collected: "cyan",
  skipped_user_locked: "default",
};

interface Props {
  namespaceId: number;
  /** 若提供，则只显示该 SCO 的审计日志 */
  scoId?: number | null;
  /** 若提供，则只显示该字段的审计日志 */
  fieldPath?: string | null;
  /** 清除过滤条件的回调 */
  onClearFilter?: () => void;
}

export function SchemaAuditTab({ namespaceId, scoId, fieldPath, onClearFilter }: Props) {
  const [entries, setEntries] = useState<SchemaAuditLogEntry[]>([]);
  const [actionFilter, setActionFilter] = useState<string[]>([]);

  useEffect(() => {
    const params: Record<string, unknown> = {};
    if (actionFilter.length > 0) params.actions = actionFilter;
    if (scoId) params.sco_id = scoId;
    if (fieldPath) params.field_path = fieldPath;

    void schemaCanonicalApi.listSchemaAuditLog(namespaceId, params as any).then(setEntries);
  }, [namespaceId, actionFilter, scoId, fieldPath]);

  const hasFilter = !!(scoId || fieldPath);

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space wrap>
        {hasFilter && (
          <Tag color="blue" closable onClose={onClearFilter}>
            {fieldPath ? `字段: ${fieldPath}` : `SCO #${scoId}`}
          </Tag>
        )}
        <Select
          mode="multiple"
          style={{ minWidth: 280 }}
          placeholder="筛选 action"
          options={SCHEMA_ACTIONS.map((a) => ({ value: a, label: a }))}
          onChange={setActionFilter}
          allowClear
        />
        {hasFilter && onClearFilter && (
          <Button size="small" onClick={onClearFilter}>
            查看全部
          </Button>
        )}
      </Space>
      {entries.length === 0 ? (
        <div style={{ color: "#999", padding: 24, textAlign: "center" }}>
          暂无审计记录
        </div>
      ) : (
        <Timeline
          items={entries.map((e) => ({
            color: ACTION_COLORS[e.action] ?? "gray",
            children: (
              <div key={e.id}>
                <Space>
                  <Tag color={ACTION_COLORS[e.action] ?? "default"}>{e.action}</Tag>
                  {e.field_path && <span>{e.field_path}</span>}
                </Space>
                {e.reason && <div style={{ color: "#666", fontSize: 12 }}>{e.reason}</div>}
                {e.before && (
                  <div style={{ color: "#ff4d4f", fontSize: 12 }}>- {JSON.stringify(e.before)}</div>
                )}
                {e.after && (
                  <div style={{ color: "#52c41a", fontSize: 12 }}>+ {JSON.stringify(e.after)}</div>
                )}
                <div style={{ color: "#999", fontSize: 12 }}>
                  {new Date(e.created_at).toLocaleString()}
                  {e.actor_id != null ? ` · 操作员 #${e.actor_id}` : " · 系统"}
                </div>
              </div>
            ),
          }))}
        />
      )}
    </Space>
  );
}
