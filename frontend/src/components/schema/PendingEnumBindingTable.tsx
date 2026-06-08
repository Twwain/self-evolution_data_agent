/**
 * PendingEnumBindingTable — 待绑定字段列表
 *
 * 展示 namespace 下所有 enum_match_status=pending 的字段, 提供绑定入口
 */
import { useEffect, useState, useCallback } from "react";
import { Table, Button, Tag, message } from "antd";
import { enumApi } from "@/api";
import type { PendingEnumBinding } from "@/types/schema-canonical";
import { EnumBindDrawer } from "./EnumBindDrawer";

interface Props {
  namespaceId: number;
}

export function PendingEnumBindingTable({ namespaceId }: Props) {
  const [items, setItems] = useState<PendingEnumBinding[]>([]);
  const [loading, setLoading] = useState(false);
  const [drawer, setDrawer] = useState<PendingEnumBinding | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await enumApi.listPendingEnumBindings(namespaceId);
      setItems(r.items);
    } catch {
      message.error("加载待绑定字段失败");
    } finally {
      setLoading(false);
    }
  }, [namespaceId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <Table
        rowKey={(r) => `${r.collection_id}_${r.field}`}
        dataSource={items}
        loading={loading}
        size="small"
        pagination={{ pageSize: 20 }}
        columns={[
          { title: "集合", dataIndex: "collection_name" },
          { title: "字段", dataIndex: "field" },
          { title: "类型", dataIndex: "field_type", width: 100 },
          {
            title: "Hint",
            dataIndex: "enum_class_hint",
            width: 160,
            render: (v: string | null | undefined) => (v ? <Tag>{v}</Tag> : null),
          },
          {
            title: "样本",
            dataIndex: "sample_values",
            width: 200,
            render: (vals: (number | string)[] | null | undefined) =>
              vals && vals.length > 0
                ? vals.map((s) => <Tag key={String(s)}>{String(s)}</Tag>)
                : "—",
          },
          {
            title: "操作",
            width: 80,
            render: (_, r) => (
              <Button size="small" type="primary" onClick={() => setDrawer(r)}>
                绑定
              </Button>
            ),
          },
        ]}
      />
      {drawer && (
        <EnumBindDrawer
          open
          collectionId={drawer.collection_id}
          fieldName={drawer.field}
          fieldType={drawer.field_type ?? ""}
          namespaceId={namespaceId}
          samples={drawer.sample_values ?? undefined}
          onClose={() => setDrawer(null)}
          onBound={() => {
            setDrawer(null);
            void load();
          }}
        />
      )}
    </>
  );
}
