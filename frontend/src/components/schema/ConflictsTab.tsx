import { Empty, Space, Tag, message } from "antd";
import { useEffect, useState } from "react";

import { schemaCanonicalApi } from "@/api";
import type { SchemaConflict } from "@/types/schema-canonical";
import { ConflictResolver, type ResolveBody } from "./ConflictResolver";

export function ConflictsTab({
  namespaceId,
  onResolved,
}: {
  namespaceId: number;
  onResolved?: () => void;
}) {
  const [conflicts, setConflicts] = useState<SchemaConflict[]>([]);

  const refresh = async () => setConflicts(await schemaCanonicalApi.listConflicts(namespaceId, "open"));
  useEffect(() => { void refresh(); }, [namespaceId]);

  const onResolve = async (cid: number, body: ResolveBody) => {
    try {
      await schemaCanonicalApi.resolveConflict(namespaceId, cid, body);
      message.success("已解决");
      await refresh();
      onResolved?.();
    } catch (err: any) {
      if (err?.response?.status === 409) {
        message.warning("该冲突已被其他人解决，正在刷新...");
        await refresh();
        onResolved?.();
      } else {
        message.error("解决冲突失败");
      }
    }
  };

  if (conflicts.length === 0) return <Empty description="暂无未解决的冲突" />;

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {conflicts.map((c) => (
        <div key={c.id} style={{ border: "1px solid #f0f0f0", padding: 16, borderRadius: 8 }}>
          <Space style={{ marginBottom: 8 }}>
            <strong>{c.field_path ? `${c.target}.${c.field_path}` : c.target}</strong>
            <Tag color={c.conflict_type === "semantic_equivalent" ? "gold" : "orange"}>
              {c.conflict_type}
            </Tag>
            <Tag>{c.candidate_kind}</Tag>
          </Space>
          <ConflictResolver conflict={c} onResolve={(body) => onResolve(c.id, body)} />
        </div>
      ))}
    </Space>
  );
}
