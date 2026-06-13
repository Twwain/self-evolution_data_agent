/* ════════════════════════════════════════════
 *  修改密码页 — 全角色可达
 * ════════════════════════════════════════════ */
import React, { useState } from "react";
import { Button, Card, Form, Input, message } from "antd";
import { changePassword } from "@/api";

/** 与后端 IS_PASSWORD_MIN_LENGTH 默认值同步 */
const PASSWORD_MIN_LENGTH = 8;

const ProfilePage: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm();

  const handleSubmit = async (values: {
    old_password: string;
    new_password: string;
    confirm: string;
  }) => {
    if (values.new_password !== values.confirm) {
      message.error("两次输入的新密码不一致");
      return;
    }
    setLoading(true);
    try {
      await changePassword({
        old_password: values.old_password,
        new_password: values.new_password,
      });
      message.success("密码修改成功");
      form.resetFields();
    } catch (err: any) {
      message.error(err.response?.data?.detail || "修改失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 480, margin: "40px auto" }}>
      <Card title="修改密码">
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Form.Item
            name="old_password"
            label="当前密码"
            rules={[{ required: true, message: "请输入当前密码" }]}
          >
            <Input.Password placeholder="当前密码" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: "请输入新密码" },
              { min: PASSWORD_MIN_LENGTH, message: `密码至少 ${PASSWORD_MIN_LENGTH} 位` },
              { pattern: /^(?=.*[A-Za-z])(?=.*\d).+$/, message: "密码必须包含字母和数字" },
            ]}
          >
            <Input.Password placeholder="至少 8 位, 字母+数字" />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="确认新密码"
            rules={[{ required: true, message: "请再次输入新密码" }]}
          >
            <Input.Password placeholder="再次输入新密码" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" loading={loading}>
              提交
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
};

export default ProfilePage;
