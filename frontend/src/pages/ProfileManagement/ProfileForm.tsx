import { useState } from "react";
import { Form, Input, Select, Button, Space, message } from "antd";
import { createProfile, updateProfile, type ProfileOut } from "@/api";

interface Props {
  profile: ProfileOut | null;
  onDone: () => void;
  onCancel: () => void;
}

export default function ProfileForm({ profile, onDone, onCancel }: Props) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const isEdit = !!profile;

  const handleSubmit = async (values: any) => {
    setSaving(true);
    try {
      if (isEdit) {
        await updateProfile(profile!.id, values);
      } else {
        await createProfile(values);
      }
      message.success(isEdit ? "已更新" : "已创建");
      onDone();
    } catch (e: any) {
      message.error(e.response?.data?.detail || e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ padding: 24, maxWidth: 640 }}>
      <h2>{isEdit ? "编辑 Profile" : "新建 Profile"}</h2>
      <Form
        form={form}
        layout="vertical"
        initialValues={profile || { languages: ["Java"], hint_text: "", description: "" }}
        onFinish={handleSubmit}
      >
        <Form.Item name="name" label="名称 (slug)" rules={[{ required: true }]}>
          <Input disabled={isEdit} placeholder="java-spring" />
        </Form.Item>
        <Form.Item name="display_name" label="显示名" rules={[{ required: true }]}>
          <Input placeholder="Java Spring (JPA + MyBatis + MongoDB)" />
        </Form.Item>
        <Form.Item name="description" label="简介">
          <Input.TextArea rows={2} placeholder="下拉列表一行说明" />
        </Form.Item>
        <Form.Item name="languages" label="语言" rules={[{ required: true }]}>
          <Select mode="tags" placeholder="Java" />
        </Form.Item>
        <Form.Item
          name="hint_text"
          label="提示文本 (Hint)"
          rules={[{ required: true }]}
          extra="自由文本，编写 agent 提取时的框架提示。自然语言即可。"
        >
          <Input.TextArea rows={6} placeholder="实体类通常标注 @Entity/@Table(JPA) 或 @Document(MongoDB)..." />
        </Form.Item>
        <Space>
          <Button type="primary" htmlType="submit" loading={saving}>
            {isEdit ? "保存" : "创建"}
          </Button>
          <Button onClick={onCancel}>取消</Button>
        </Space>
      </Form>
    </div>
  );
}
