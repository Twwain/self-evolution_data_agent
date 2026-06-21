import { useEffect, useState } from "react";
import { Button, Table, Tabs, Tag, Space, message, Popconfirm } from "antd";
import { PlusOutlined, EditOutlined, DeleteOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { fetchProfiles, deleteProfile, type ProfileOut } from "@/api";
import ProfileForm from "./ProfileForm";

export default function ProfileManagement() {
  const [profiles, setProfiles] = useState<ProfileOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<ProfileOut | null>(null);
  const [creating, setCreating] = useState(false);

  const loadProfiles = async () => {
    setLoading(true);
    try {
      setProfiles(await fetchProfiles());
    } catch (e: any) {
      message.error("加载失败: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadProfiles();
  }, []);

  const handleDelete = async (id: number) => {
    try {
      await deleteProfile(id);
      message.success("已删除");
      loadProfiles();
    } catch (e: any) {
      message.error(e.response?.data?.detail || "删除失败");
    }
  };

  const builtins = profiles.filter((p) => p.is_builtin);
  const customs = profiles.filter((p) => !p.is_builtin);

  const columns: ColumnsType<ProfileOut> = [
    { title: "名称", dataIndex: "name", key: "name" },
    { title: "显示名", dataIndex: "display_name", key: "display_name" },
    {
      title: "语言", dataIndex: "languages", key: "languages",
      render: (langs: string[]) => langs.map((l) => <Tag key={l}>{l}</Tag>),
    },
    {
      title: "提示长度", dataIndex: "hint_text", key: "hint_text",
      render: (t: string) => (t.length > 60 ? t.slice(0, 60) + "..." : t),
    },
    {
      title: "启用", dataIndex: "is_enabled", key: "is_enabled",
      render: (v: boolean) =>
        v ? <Tag color="green">启用</Tag> : <Tag color="red">禁用</Tag>,
    },
    {
      title: "操作", key: "actions",
      render: (_, record) => (
        <Space>
          <Button icon={<EditOutlined />} size="small" onClick={() => setEditing(record)}>
            编辑
          </Button>
          {!record.is_builtin && (
            <Popconfirm title="确认删除?" onConfirm={() => handleDelete(record.id)}>
              <Button icon={<DeleteOutlined />} size="small" danger>
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  if (editing || creating) {
    return (
      <ProfileForm
        profile={editing}
        onDone={() => {
          setEditing(null);
          setCreating(false);
          loadProfiles();
        }}
        onCancel={() => {
          setEditing(null);
          setCreating(false);
        }}
      />
    );
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
        <h2>Profile 管理</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
          新建 Profile
        </Button>
      </div>
      <Tabs
        items={[
          {
            key: "builtin",
            label: `内置模板 (${builtins.length})`,
            children: (
              <Table columns={columns} dataSource={builtins} rowKey="id" loading={loading} pagination={false} />
            ),
          },
          {
            key: "custom",
            label: `自定义 (${customs.length})`,
            children: (
              <Table columns={columns} dataSource={customs} rowKey="id" loading={loading} pagination={false} />
            ),
          },
        ]}
      />
    </div>
  );
}
