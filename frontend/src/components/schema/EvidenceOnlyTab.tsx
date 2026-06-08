import { Button, Checkbox, Space, Table, message } from "antd";
import { useEffect, useMemo, useState } from "react";

import { schemaCanonicalApi } from "@/api";
import type { EvidenceOnlyField } from "@/types/schema-canonical";
import { EvidenceDrawer } from "./EvidenceDrawer";

export function EvidenceOnlyTab({ namespaceId }: { namespaceId: number }) {
  const [rows, setRows] = useState<EvidenceOnlyField[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [drawer, setDrawer] = useState<{ scoId: number; field: string } | null>(null);

  const refresh = async () => setRows(await schemaCanonicalApi.listEvidenceOnlyFields(namespaceId));
  useEffect(() => { void refresh(); }, [namespaceId]);

  const allSelected = useMemo(
    () => rows.length > 0 && selected.size === rows.length,
    [rows, selected],
  );

  const toggleAll = () => {
    setSelected(allSelected ? new Set() : new Set(rows.map((r) => `${r.sco_id}:${r.field_path}`)));
  };

  const bulk = async (action: "confirm" | "ignore") => {
    for (const r of rows) {
      const key = `${r.sco_id}:${r.field_path}`;
      if (!selected.has(key)) continue;
      await schemaCanonicalApi.confirmField(namespaceId, r.sco_id, { field_path: r.field_path, action });
    }
    message.success(`批量${action === "confirm" ? "确认" : "忽略"}完成`);
    setSelected(new Set());
    await refresh();
  };

  const singleAction = async (r: EvidenceOnlyField, action: "confirm" | "ignore") => {
    await schemaCanonicalApi.confirmField(namespaceId, r.sco_id, { field_path: r.field_path, action });
    message.success(action === "confirm" ? "已确认" : "已忽略");
    await refresh();
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space>
        <Checkbox checked={allSelected} onChange={toggleAll} aria-label="全选">全选当前页</Checkbox>
        <Button onClick={() => bulk("confirm")} disabled={selected.size === 0}>批量确认</Button>
        <Button danger onClick={() => bulk("ignore")} disabled={selected.size === 0}>批量忽略</Button>
      </Space>
      <Table<EvidenceOnlyField>
        rowKey={(r) => `${r.sco_id}:${r.field_path}`}
        size="small"
        dataSource={rows}
        rowSelection={{
          selectedRowKeys: [...selected],
          onChange: (keys) => setSelected(new Set(keys.map(String))),
        }}
        columns={[
          {
            title: "字段",
            render: (_, r) => (
              <a onClick={() => setDrawer({ scoId: r.sco_id, field: r.field_path })}>
                {r.target}.{r.field_path}
              </a>
            ),
          },
          { title: "当前值", render: (_, r) => r.current_value.description || "—" },
          { title: "证据", dataIndex: "evidence_summary" },
          {
            title: "操作",
            render: (_, r) => (
              <Space size={4}>
                <Button size="small" onClick={() => singleAction(r, "confirm")}>确认</Button>
                <Button size="small" onClick={() => setDrawer({ scoId: r.sco_id, field: r.field_path })}>修正</Button>
                <Button size="small" danger onClick={() => singleAction(r, "ignore")}>忽略</Button>
              </Space>
            ),
          },
        ]}
      />
      {drawer && (
        <EvidenceDrawer
          namespaceId={namespaceId}
          scoId={drawer.scoId}
          fieldPath={drawer.field}
          open={true}
          onClose={() => { setDrawer(null); void refresh(); }}
        />
      )}
    </Space>
  );
}
