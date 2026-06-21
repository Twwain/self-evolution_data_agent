/**
 * EnumEditorModal — 新建/编辑枚举弹窗
 *
 * 字段: enum_class_name, db_value 类型选择, 备注, values 表格编辑器
 */
import { useState, useEffect } from "react";
import { Modal, Form, Input, Button, Table, InputNumber, Select, Space } from "antd";
import type { EnumValueItem } from "@/types/schema-canonical";

interface Props {
  open: boolean;
  mode: "create" | "edit";
  namespaceId: number;
  dbType: import("@/types").DbType;
  initial?: { enum_class_name: string; values: EnumValueItem[]; comment?: string };
  onClose: () => void;
  onSubmit: (payload: {
    enum_class_name: string;
    values: EnumValueItem[];
    comment?: string;
  }) => Promise<void>;
}

export function EnumEditorModal({ open, mode, initial, onClose, onSubmit }: Props) {
  const [name, setName] = useState(initial?.enum_class_name ?? "");
  const [comment, setComment] = useState(initial?.comment ?? "");
  const [values, setValues] = useState<EnumValueItem[]>(initial?.values ?? []);
  const [valueKind, setValueKind] = useState<"int" | "string">("int");
  const [submitting, setSubmitting] = useState(false);

  // Reset form when modal opens with new initial data
  useEffect(() => {
    if (open) {
      setName(initial?.enum_class_name ?? "");
      setComment(initial?.comment ?? "");
      setValues(initial?.values ?? []);
    }
  }, [open, initial]);

  const canSubmit =
    name.trim().length > 0 &&
    values.length > 0 &&
    values.every((v) => v.name.trim().length > 0);

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await onSubmit({
        enum_class_name: name.trim(),
        values,
        comment: comment.trim() || undefined,
      });
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={mode === "create" ? "新建枚举" : "编辑枚举"}
      open={open}
      onCancel={onClose}
      okText="确定"
      cancelText="取消"
      okButtonProps={{ disabled: !canSubmit, loading: submitting }}
      onOk={handleSubmit}
      width={720}
      destroyOnHidden
    >
      <Form layout="vertical">
        <Form.Item label="enum_class_name" required>
          <Input
            id="enum_class_name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={mode === "edit"}
            placeholder="如 OrderStatus"
          />
        </Form.Item>
        <Form.Item label="db_value 类型">
          <Select
            value={valueKind}
            onChange={setValueKind}
            options={[
              { value: "int", label: "整数" },
              { value: "string", label: "字符串" },
            ]}
          />
        </Form.Item>
        <Form.Item label="备注">
          <Input.TextArea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            maxLength={500}
            rows={2}
          />
        </Form.Item>
        <Form.Item label="枚举值">
          <Table
            rowKey="_key"
            dataSource={values.map((v, i) => ({ ...v, _key: `row-${i}` }))}
            pagination={false}
            size="small"
            columns={[
              {
                title: "name",
                dataIndex: "name",
                render: (v: string, _row, idx) => (
                  <Input
                    value={v}
                    onChange={(e) => {
                      const next = [...values];
                      next[idx] = { ...next[idx], name: e.target.value };
                      setValues(next);
                    }}
                    placeholder="枚举名"
                  />
                ),
              },
              {
                title: "db_value",
                dataIndex: "db_value",
                render: (v: number | string, _row, idx) =>
                  valueKind === "int" ? (
                    <InputNumber
                      value={v as number}
                      onChange={(n) => {
                        const next = [...values];
                        next[idx] = { ...next[idx], db_value: n ?? 0 };
                        setValues(next);
                      }}
                    />
                  ) : (
                    <Input
                      value={v as string}
                      onChange={(e) => {
                        const next = [...values];
                        next[idx] = { ...next[idx], db_value: e.target.value };
                        setValues(next);
                      }}
                    />
                  ),
              },
              {
                title: "description",
                dataIndex: "description",
                render: (v: string | null | undefined, _row, idx) => (
                  <Input
                    value={v ?? ""}
                    onChange={(e) => {
                      const next = [...values];
                      next[idx] = { ...next[idx], description: e.target.value || null };
                      setValues(next);
                    }}
                    placeholder="描述"
                  />
                ),
              },
              {
                title: "操作",
                width: 60,
                render: (_, __, idx) => (
                  <Button
                    danger
                    size="small"
                    onClick={() => setValues(values.filter((_, i) => i !== idx))}
                  >
                    删除
                  </Button>
                ),
              },
            ]}
          />
          <Space style={{ marginTop: 8 }}>
            <Button
              onClick={() =>
                setValues([
                  ...values,
                  { name: "", db_value: valueKind === "int" ? 0 : "", description: null },
                ])
              }
            >
              添加值
            </Button>
          </Space>
        </Form.Item>
      </Form>
    </Modal>
  );
}
