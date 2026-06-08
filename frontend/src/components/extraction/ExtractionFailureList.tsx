import { Button, Collapse, Empty, Space, Tag, message } from "antd";
import { useEffect, useState } from "react";

import { schemaCanonicalApi } from "@/api";
import type { ExtractionFailure } from "@/types/schema-canonical";

export function ExtractionFailureList({ namespaceId }: { namespaceId: number }) {
  const [rows, setRows] = useState<ExtractionFailure[]>([]);

  const refresh = async () => setRows(await schemaCanonicalApi.listExtractionFailures(namespaceId));
  useEffect(() => { void refresh(); }, [namespaceId]);

  const onRetry = async (id: number) => {
    await schemaCanonicalApi.retryExtractionFailure(id);
    message.success("已重试");
    await refresh();
  };

  const onIgnore = async (id: number) => {
    await schemaCanonicalApi.ignoreExtractionFailure(id);
    message.success("已忽略");
    await refresh();
  };

  if (rows.length === 0) return <Empty description="暂无抽取失败" />;

  // Group by extraction_kind
  const byKind = rows.reduce<Record<string, ExtractionFailure[]>>((m, r) => {
    (m[r.extraction_kind] ??= []).push(r);
    return m;
  }, {});

  return (
    <Collapse
      defaultActiveKey={Object.keys(byKind)}
      items={Object.entries(byKind).map(([kind, list]) => ({
        key: kind,
        label: `${kind} (${list.length})`,
        children: (
          <Space direction="vertical" style={{ width: "100%" }}>
            {list.map((r) => (
              <div key={r.id} style={{ border: "1px solid #f0f0f0", padding: 12, borderRadius: 6 }}>
                <Space>
                  <Tag color="red">{r.failure_type}</Tag>
                  <strong>
                    {r.source_mapper && r.source_method
                      ? `${r.source_mapper}.${r.source_method}`
                      : r.source_file ?? "—"}
                  </strong>
                </Space>
                <div style={{ color: "#888", fontSize: 12, marginTop: 4 }}>{r.failure_message}</div>
                {r.source_content && (
                  <Collapse ghost size="small" items={[{
                    key: "src",
                    label: "展开 SQL",
                    children: <pre style={{ background: "#f5f5f5", padding: 8, fontSize: 12 }}>{r.source_content}</pre>,
                  }]} />
                )}
                <Space style={{ marginTop: 8 }}>
                  <Button
                    size="small"
                    onClick={() => onRetry(r.id)}
                    disabled={r.retry_count >= 999}
                  >
                    重试
                  </Button>
                  <Button size="small" danger onClick={() => onIgnore(r.id)}>忽略</Button>
                </Space>
              </div>
            ))}
          </Space>
        ),
      }))}
    />
  );
}
