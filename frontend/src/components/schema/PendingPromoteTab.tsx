import { Alert, Button, Collapse, Space, Table, Tag } from "antd";
import { useEffect, useState } from "react";

import { schemaCanonicalApi } from "@/api";
import type { PendingCandidateGroup } from "@/types/schema-canonical";

export function PendingPromoteTab({ namespaceId }: { namespaceId: number }) {
  const [groups, setGroups] = useState<PendingCandidateGroup[]>([]);
  const [report, setReport] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setGroups(await schemaCanonicalApi.listPendingCandidates(namespaceId));
  };

  useEffect(() => { void refresh(); }, [namespaceId]);

  const onPromote = async () => {
    setLoading(true);
    try {
      const r = await schemaCanonicalApi.promote(namespaceId);
      setReport(`promoted: ${r.promoted_count}, conflicts: ${r.conflicted_count}, processed: ${r.candidates_processed}`);
      await refresh();
    } finally {
      setLoading(false);
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space>
        <Button type="primary" loading={loading} onClick={onPromote}>立即汇聚</Button>
        {report && <Alert type="success" message={`汇聚完成 — ${report}`} showIcon closable />}
      </Space>
      <Table<PendingCandidateGroup>
        rowKey="id"
        size="small"
        dataSource={groups}
        columns={[
          {
            title: "字段",
            render: (_, r) => <span>{r.target}.{r.field_path}</span>,
          },
          {
            title: "候选数",
            render: (_, r) => `${r.candidates.length} 个候选`,
          },
          {
            title: "来源",
            render: (_, r) => (
              <Space size={4}>
                {[...new Set(r.candidates.map((c) => c.source))].map((s) => (
                  <Tag key={s}>{s}</Tag>
                ))}
              </Space>
            ),
          },
        ]}
        expandable={{
          expandedRowRender: (r) => (
            <Collapse size="small" defaultActiveKey={["1"]} items={[{
              key: "1",
              label: "候选详情",
              children: (
                <Space direction="vertical" style={{ width: "100%" }}>
                  {r.candidates.map((c) => (
                    <div key={c.id}>
                      <Tag color="blue">{c.source}</Tag>
                      {c.value.description ?? JSON.stringify(c.value)}
                    </div>
                  ))}
                </Space>
              ),
            }]} />
          ),
          rowExpandable: () => true,
        }}
      />
    </Space>
  );
}
