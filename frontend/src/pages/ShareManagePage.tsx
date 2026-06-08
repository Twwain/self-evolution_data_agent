/* ════════════════════════════════════════════
 *  分享管理页 — 列出所有分享链接, 支持停用操作
 * ════════════════════════════════════════════ */

import React, { useEffect, useState } from "react";
import { Table, Button, Tag, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { listShares, deactivateShare } from "@/api";

interface ShareRecord {
  id: number;
  token: string;
  query_history_id: number;
  shared_by: number;
  created_at: string;
  expires_at: string | null;
  is_active: boolean;
}

function getStatus(record: ShareRecord): { label: string; color: string } {
  if (!record.is_active) return { label: "已停用", color: "default" };
  if (record.expires_at && new Date(record.expires_at) < new Date()) {
    return { label: "已过期", color: "orange" };
  }
  return { label: "有效", color: "green" };
}

const ShareManagePage: React.FC = () => {
  const [data, setData] = useState<ShareRecord[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const res = await listShares();
      setData(res);
    } catch {
      message.error("加载分享列表失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleDeactivate = async (token: string) => {
    try {
      await deactivateShare(token);
      message.success("已停用");
      load();
    } catch {
      message.error("停用失败");
    }
  };

  const columns: ColumnsType<ShareRecord> = [
    {
      title: "Token",
      dataIndex: "token",
      width: 140,
      render: (t: string) => t.slice(0, 8) + "…",
    },
    {
      title: "关联查询ID",
      dataIndex: "query_history_id",
      width: 120,
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 180,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: "过期时间",
      dataIndex: "expires_at",
      width: 180,
      render: (v: string | null) => (v ? new Date(v).toLocaleString() : "永不过期"),
    },
    {
      title: "状态",
      width: 100,
      render: (_: unknown, record: ShareRecord) => {
        const { label, color } = getStatus(record);
        return <Tag color={color}>{label}</Tag>;
      },
    },
    {
      title: "操作",
      width: 100,
      render: (_: unknown, record: ShareRecord) => {
        const status = getStatus(record);
        if (status.label !== "有效") return null;
        return (
          <Button size="small" danger onClick={() => handleDeactivate(record.token)}>
            停用
          </Button>
        );
      },
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <h2>分享管理</h2>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={data}
        loading={loading}
        pagination={{ pageSize: 20 }}
      />
    </div>
  );
};

export default ShareManagePage;
