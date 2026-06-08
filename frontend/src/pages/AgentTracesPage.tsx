/* ════════════════════════════════════════════
 *  Stage 2 抓手 E — Agent Traces 运维页面
 *  含 Stage 2 抓手 C: reflection_log 详情 Modal
 * ════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { Button, Modal, Select, Space, Table, Tag, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { fetchNamespaces, getAgentTrace, listAgentTraces } from "@/api";
import type { Namespace } from "@/types";

interface TraceRow {
  id: number;
  trace_id: string;
  namespace_id: number | null;
  user_query: string;
  status: string;
  created_at: string;
  tool_call_count: number;
}

interface ReflectionEntry {
  tool_name: string;
  confidence: number | null;
  reason: string;
  alternative?: string;
}

interface TraceDetail {
  trace_id: string;
  user_query: string;
  trace_json: string;
  reflection_log_json: string;
  status: string;
  refined_at: string | null;
  refined_summary: string | null;
  created_at: string;
}

function TraceDetailModal({
  traceId,
  onClose,
}: {
  traceId: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<TraceDetail | null>(null);

  useEffect(() => {
    getAgentTrace(traceId).then(setDetail).catch(() => message.error("加载详情失败"));
  }, [traceId]);

  if (!detail) return null;

  let reflections: ReflectionEntry[] = [];
  try {
    reflections = JSON.parse(detail.reflection_log_json || "[]");
  } catch {
    /* ignore parse error */
  }

  const reflectionColumns: ColumnsType<ReflectionEntry> = [
    { title: "Tool", dataIndex: "tool_name", width: 180 },
    {
      title: "Confidence",
      dataIndex: "confidence",
      width: 100,
      render: (v: number | null) => (v !== null ? v.toFixed(2) : "—"),
    },
    { title: "Reason", dataIndex: "reason" },
    { title: "Alternative", dataIndex: "alternative", width: 200 },
  ];

  return (
    <Modal
      open
      onCancel={onClose}
      footer={null}
      title={`Trace: ${traceId}`}
      width={960}
    >
      <p>
        <strong>Query:</strong> {detail.user_query}
      </p>
      <p>
        <strong>Status:</strong> <Tag>{detail.status}</Tag>
      </p>

      <h4>Reflection Log ({reflections.length})</h4>
      <Table
        size="small"
        dataSource={reflections}
        rowKey={(_, i) => String(i)}
        columns={reflectionColumns}
        pagination={false}
      />

      <h4 style={{ marginTop: 16 }}>Trace JSON</h4>
      <pre style={{ maxHeight: 400, overflow: "auto", fontSize: 12 }}>
        {detail.trace_json}
      </pre>
    </Modal>
  );
}

export default function AgentTracesPage() {
  const [rows, setRows] = useState<TraceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [namespaces, setNamespaces] = useState<Namespace[]>([]);
  const [namespaceId, setNamespaceId] = useState<number | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([]);
  const [refining, setRefining] = useState(false);

  useEffect(() => {
    fetchNamespaces().then(setNamespaces).catch(() => {});
  }, []);

  const load = async () => {
    setLoading(true);
    try {
      const data = await listAgentTraces({ namespace_id: namespaceId, status: statusFilter, size: 100 });
      setRows(data);
    } catch {
      message.error("加载 traces 失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [statusFilter, namespaceId]);

  const handleRefine = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning("请先选择要提炼的 traces");
      return;
    }
    setRefining(true);
    try {
      const { refineAgentTraces } = await import("@/api");
      const res = await refineAgentTraces(selectedRowKeys);
      message.success(`提炼完成: 产生 ${res.proposed_count} 条知识提案`);
      setSelectedRowKeys([]);
      load();
    } catch {
      message.error("批量提炼失败");
    } finally {
      setRefining(false);
    }
  };

  const columns: ColumnsType<TraceRow> = [
    { title: "Trace ID", dataIndex: "trace_id", width: 220, ellipsis: true },
    { title: "Query", dataIndex: "user_query", ellipsis: true },
    {
      title: "Status",
      dataIndex: "status",
      width: 100,
      render: (s: string) => (
        <Tag color={s === "completed" ? "green" : s === "failed" ? "red" : "default"}>
          {s}
        </Tag>
      ),
    },
    { title: "Tools", dataIndex: "tool_call_count", width: 70 },
    { title: "Created", dataIndex: "created_at", width: 180 },
    {
      title: "Action",
      width: 80,
      render: (_, row) => (
        <Button size="small" type="link" onClick={() => setSelectedTraceId(row.trace_id)}>
          详情
        </Button>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <h2>Agent Traces</h2>
      <Space style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="Namespace"
          style={{ width: 160 }}
          value={namespaceId}
          onChange={setNamespaceId}
          options={namespaces.map((ns) => ({ label: ns.name, value: ns.id }))}
        />
        <Select
          allowClear
          placeholder="Status"
          style={{ width: 140 }}
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { label: "completed", value: "completed" },
            { label: "failed", value: "failed" },
            { label: "cancelled", value: "cancelled" },
            { label: "refined", value: "refined" },
          ]}
        />
        <Button onClick={load}>刷新</Button>
        <Button
          type="primary"
          disabled={selectedRowKeys.length === 0}
          loading={refining}
          onClick={handleRefine}
        >
          批量提炼 ({selectedRowKeys.length})
        </Button>
      </Space>
      <Table
        size="small"
        loading={loading}
        dataSource={rows}
        columns={columns}
        rowKey="trace_id"
        pagination={{ pageSize: 50 }}
        rowSelection={{
          selectedRowKeys,
          onChange: (keys) => setSelectedRowKeys(keys as string[]),
          getCheckboxProps: (row) => ({
            disabled: row.status !== "completed",
          }),
        }}
      />
      {selectedTraceId && (
        <TraceDetailModal
          traceId={selectedTraceId}
          onClose={() => setSelectedTraceId(null)}
        />
      )}
    </div>
  );
}
