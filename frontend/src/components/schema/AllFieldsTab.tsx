import { Button, Input, message, Modal, Space, Table, Tag } from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";

import { enumApi } from "@/api";
import type {
  SchemaCanonicalField,
  SchemaCanonicalObject,
  SchemaCanonicalRelationship,
} from "@/types/schema-canonical";
import { ConfidenceTag } from "./ConfidenceTag";
import { EnumBindDrawer } from "./EnumBindDrawer";
import { FieldRowActions } from "./FieldRowActions";

/* ── Internal types ── */

interface EditableField extends SchemaCanonicalField {
  _isNew?: boolean;
  _key: string;
}

/* ── Validation ── */

function validateFields(
  fields: EditableField[],
  prefix = "",
): Record<string, string> {
  const errors: Record<string, string> = {};
  const names = new Set<string>();

  for (const f of fields) {
    const key = prefix + f._key;
    const name = (f.name ?? "").trim();

    if (f._isNew) {
      if (!name) {
        errors[`${key}_name`] = "字段名不能为空";
      } else if (name.length > 64) {
        errors[`${key}_name`] = "字段名不能超过 64 字符";
      } else if (names.has(name)) {
        errors[`${key}_name`] = "字段名重复";
      }
    }
    if (name) names.add(name);

    if (!(f.type ?? "").trim()) {
      errors[`${key}_type`] = "类型不能为空";
    }

    // Recursive sub-field validation
    if (f.sub_fields && f.sub_fields.length > 0) {
      const subErrors = validateFields(
        f.sub_fields as EditableField[],
        `${key}_sub_`,
      );
      Object.assign(errors, subErrors);
    }
  }
  return errors;
}

function toEditableFields(fields: SchemaCanonicalField[]): EditableField[] {
  return fields.map((f) => ({
    ...f,
    _key: f.name || crypto.randomUUID(),
    _isNew: false,
    sub_fields: f.sub_fields
      ? toEditableFields(f.sub_fields)
      : undefined,
  }));
}

function stripEditFlags(fields: EditableField[]): SchemaCanonicalField[] {
  return fields.map(({ _isNew, _key, sub_fields, ...rest }) => ({
    ...rest,
    ...(sub_fields && sub_fields.length > 0
      ? { sub_fields: stripEditFlags(sub_fields as EditableField[]) }
      : {}),
  }));
}

/* ── 递归子字段树 ── */

function getFieldName(f: SchemaCanonicalField): string {
  return f.name || "";
}

function SubFieldsTree(props: {
  subFields: EditableField[];
  editing: boolean;
  onSubFieldChange: (updated: EditableField[]) => void;
  depth?: number;
  validationErrors?: Record<string, string>;
  parentKey?: string;
}) {
  const { subFields, editing, onSubFieldChange, depth = 0, validationErrors = {}, parentKey = "" } = props;
  if (!subFields.length && !editing) return null;

  const handleAddSubField = () => {
    if (depth >= 5) {
      message.warning("已达最大嵌套深度");
      return;
    }
    const newField: EditableField = {
      name: "",
      type: "",
      description: "",
      _isNew: true,
      _key: crypto.randomUUID(),
    };
    onSubFieldChange([...subFields, newField]);
  };

  const handleDelete = (key: string) => {
    onSubFieldChange(subFields.filter((f) => f._key !== key));
  };

  return (
    <div style={{ paddingLeft: depth > 0 ? 16 : 24 }}>
      <Table<EditableField>
        size="small"
        dataSource={subFields}
        rowKey={(r) => r._key}
        pagination={false}
        showHeader={depth === 0}
        columns={[
          {
            title: "字段",
            dataIndex: "name",
            width: 160,
            render: (_: unknown, r: EditableField, idx: number) => {
              if (editing && r._isNew) {
                const errKey = `${parentKey}${r._key}_name`;
                return (
                  <Input
                    size="small"
                    value={r.name}
                    status={validationErrors[errKey] ? "error" : undefined}
                    onChange={(e) => {
                      const next = [...subFields];
                      next[idx] = { ...next[idx], name: e.target.value };
                      onSubFieldChange(next);
                    }}
                    placeholder="字段名"
                  />
                );
              }
              return getFieldName(r);
            },
          },
          {
            title: "类型",
            dataIndex: "type",
            width: 120,
            render: (text: string, _r: EditableField, idx: number) => {
              if (editing) {
                const errKey = `${parentKey}${subFields[idx]._key}_type`;
                return (
                  <Input
                    size="small"
                    value={subFields[idx]?.type || ""}
                    status={validationErrors[errKey] ? "error" : undefined}
                    onChange={(e) => {
                      const next = [...subFields];
                      next[idx] = { ...next[idx], type: e.target.value };
                      onSubFieldChange(next);
                    }}
                    placeholder="类型"
                  />
                );
              }
              return text || "—";
            },
          },
          {
            title: "描述",
            dataIndex: "description",
            render: (text: string, _record: EditableField, idx: number) => {
              if (editing) {
                return (
                  <Input
                    size="small"
                    value={subFields[idx]?.description || ""}
                    onChange={(e) => {
                      const next = [...subFields];
                      next[idx] = { ...next[idx], description: e.target.value };
                      onSubFieldChange(next);
                    }}
                    placeholder="描述..."
                  />
                );
              }
              return text || <span style={{ color: "#999" }}>—</span>;
            },
          },
          {
            title: "索引",
            dataIndex: "indexed",
            width: 60,
            render: (v: boolean) => (v ? <Tag color="blue">✓</Tag> : null),
          },
          {
            title: "可信度",
            dataIndex: "description_confidence",
            width: 100,
            render: (s: string) => (s ? <ConfidenceTag status={s as SchemaCanonicalField["description_confidence"] & string} /> : "—"),
          },
          ...(editing
            ? [{
                title: "操作",
                width: 60,
                render: (_: unknown, r: EditableField) => (
                  <Button
                    type="text"
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={() => handleDelete(r._key)}
                  />
                ),
              }]
            : []),
        ]}
        expandable={{
          rowExpandable: (r) => !!(r.sub_fields?.length || r.enum_values?.length || editing),
          expandedRowRender: (record) => {
            const idx = subFields.findIndex((f) => f._key === record._key);
            return (
              <div>
                {(record.sub_fields && record.sub_fields.length > 0 || editing) && (
                  <SubFieldsTree
                    subFields={(record.sub_fields as EditableField[]) || []}
                    editing={editing}
                    depth={depth + 1}
                    validationErrors={validationErrors}
                    parentKey={`${parentKey}${record._key}_sub_`}
                    onSubFieldChange={(updatedNested) => {
                      const next = [...subFields];
                      next[idx] = { ...next[idx], sub_fields: updatedNested };
                      onSubFieldChange(next);
                    }}
                  />
                )}
                {record.enum_values && record.enum_values.length > 0 && (
                  <div style={{ padding: "4px 24px" }}>
                    <strong>枚举值: </strong>
                    {record.enum_values.map((v, i) => (
                      <Tag key={i}>
                        {v.db_value}
                        {v.name ? ` (${v.name})` : ""}
                      </Tag>
                    ))}
                  </div>
                )}
              </div>
            );
          },
        }}
      />
      {editing && (
        <Button
          type="dashed"
          size="small"
          icon={<PlusOutlined />}
          onClick={handleAddSubField}
          style={{ marginTop: 4 }}
        >
          添加子字段
        </Button>
      )}
    </div>
  );
}

/* ── 主组件 ── */

export function AllFieldsTab(props: {
  sco: Pick<SchemaCanonicalObject, "id" | "fields" | "user_locked" | "description" | "purpose_detail" | "relationships">;
  namespaceId: number;
  onOpenEvidence: (fieldName: string) => void;
  onOpenHistory: (fieldName: string) => void;
  onLockField: (fieldName: string, locked: boolean) => void;
  onSave?: (payload: { description?: string; purpose_detail?: string; fields?: SchemaCanonicalField[] }) => Promise<void>;
  onRefresh?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editFields, setEditFields] = useState<EditableField[]>([]);
  const [editDescription, setEditDescription] = useState("");
  const [editPurposeDetail, setEditPurposeDetail] = useState("");
  const [saving, setSaving] = useState(false);
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  // Enum binding drawer state
  const [bindDrawer, setBindDrawer] = useState<{ field: SchemaCanonicalField } | null>(null);
  const [unbinding, setUnbinding] = useState<string | null>(null);

  const startEdit = () => {
    setEditFields(toEditableFields(props.sco.fields));
    setEditDescription(props.sco.description || "");
    setEditPurposeDetail(props.sco.purpose_detail || "");
    setValidationErrors({});
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setEditFields([]);
    setValidationErrors({});
  };

  const saveEdit = async () => {
    const errors = validateFields(editFields);
    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors);
      return;
    }

    setSaving(true);
    try {
      const cleanFields = stripEditFlags(editFields);
      await props.onSave?.({
        description: editDescription,
        purpose_detail: editPurposeDetail,
        fields: cleanFields,
      });
      setEditing(false);
      setValidationErrors({});
    } catch {
      // Stay in edit mode — parent shows error toast
    } finally {
      setSaving(false);
    }
  };

  const handleAddField = () => {
    const newField: EditableField = {
      name: "",
      type: "",
      description: "",
      _isNew: true,
      _key: crypto.randomUUID(),
    };
    const next = [...editFields, newField];
    setEditFields(next);
    setValidationErrors(validateFields(next));
  };

  const handleDeleteField = (key: string) => {
    const next = editFields.filter((f) => f._key !== key);
    setEditFields(next);
    setValidationErrors(validateFields(next));
  };

  const handleFieldChange = (idx: number, patch: Partial<EditableField>) => {
    const next = [...editFields];
    next[idx] = { ...next[idx], ...patch };
    setEditFields(next);
    setValidationErrors(validateFields(next));
  };

  const handleSubFieldChange = (idx: number, updatedSubs: EditableField[]) => {
    const next = [...editFields];
    next[idx] = { ...next[idx], sub_fields: updatedSubs };
    setEditFields(next);
    setValidationErrors(validateFields(next));
  };

  // Enum binding handlers
  const handleBindEnum = (field: SchemaCanonicalField) => {
    setBindDrawer({ field });
  };

  const handleUnbindEnum = (fieldName: string) => {
    Modal.confirm({
      title: "确认解绑",
      content: `确定要解除字段 "${fieldName}" 的枚举绑定吗？`,
      okText: "解绑",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        setUnbinding(fieldName);
        try {
          await enumApi.unbindFieldEnum(props.namespaceId, props.sco.id, fieldName);
          message.success("解绑成功");
          props.onRefresh?.();
        } catch {
          message.error("解绑失败");
        } finally {
          setUnbinding(null);
        }
      },
    });
  };

  const dataSource = editing ? editFields : toEditableFields(props.sco.fields);
  const hasErrors = Object.keys(validationErrors).length > 0;

  const columns: ColumnsType<EditableField> = [
    {
      title: "字段",
      dataIndex: "name",
      width: 200,
      render: (_name: string, row: EditableField, idx: number) => {
        if (editing && row._isNew) {
          const errKey = `${row._key}_name`;
          return (
            <Input
              size="small"
              value={row.name}
              status={validationErrors[errKey] ? "error" : undefined}
              onChange={(e) => handleFieldChange(idx, { name: e.target.value })}
              placeholder="字段名"
            />
          );
        }
        return (
          <span>
            <strong>{getFieldName(row)}</strong>{" "}
            {!editing && (
              <FieldRowActions
                onEvidence={() => props.onOpenEvidence(getFieldName(row))}
                onHistory={() => props.onOpenHistory(getFieldName(row))}
                onLock={() => props.onLockField(getFieldName(row), !row.user_locked)}
                userLocked={Boolean(row.user_locked)}
              />
            )}
          </span>
        );
      },
    },
    {
      title: "类型",
      dataIndex: "type",
      width: 120,
      render: (text: string, row: EditableField, idx: number) => {
        if (editing) {
          const errKey = `${row._key}_type`;
          return (
            <Input
              size="small"
              value={editFields[idx]?.type || ""}
              status={validationErrors[errKey] ? "error" : undefined}
              onChange={(e) => handleFieldChange(idx, { type: e.target.value })}
              placeholder="类型"
            />
          );
        }
        return text || "—";
      },
    },
    {
      title: "描述",
      dataIndex: "description",
      render: (text: string, _record: EditableField, idx: number) => {
        if (editing) {
          return (
            <Input
              size="small"
              value={editFields[idx]?.description || ""}
              onChange={(e) => handleFieldChange(idx, { description: e.target.value })}
              placeholder="输入字段描述..."
            />
          );
        }
        return text || <span style={{ color: "#999" }}>—</span>;
      },
    },
    {
      title: "索引",
      dataIndex: "indexed",
      width: 60,
      render: (v: boolean) => (v ? <Tag color="blue">✓</Tag> : null),
    },
    {
      title: "可信度",
      dataIndex: "description_confidence",
      width: 100,
      render: (s) => (s ? <ConfidenceTag status={s} /> : "—"),
    },
    {
      title: "枚举",
      dataIndex: "enum_values",
      width: 80,
      render: (vals: SchemaCanonicalField["enum_values"]) =>
        vals && vals.length > 0 ? `枚举: ${vals.length}` : "—",
    },
    {
      title: "Enum 绑定",
      width: 160,
      render: (_, field: EditableField) => {
        const status = field.enum_match_status;
        if (status === "matched") {
          return (
            <Space size={4}>
              <Tag color="green">已绑定</Tag>
              <Button
                size="small"
                loading={unbinding === getFieldName(field)}
                onClick={() => handleUnbindEnum(getFieldName(field))}
              >
                解绑
              </Button>
            </Space>
          );
        }
        if (status === "pending") {
          return (
            <Space size={4}>
              <Tag color="default">
                未绑定{field.enum_class_hint ? ` [${field.enum_class_hint}]` : ""}
              </Tag>
              <Button
                size="small"
                type="primary"
                onClick={() => handleBindEnum(field)}
              >
                绑定
              </Button>
            </Space>
          );
        }
        if (status === "conflict") {
          return <Tag color="red">冲突</Tag>;
        }
        return "—";
      },
    },
    {
      title: "Source",
      dataIndex: "enum_source",
      width: 100,
      render: (s: string | null | undefined) => {
        if (!s) return null;
        const colorMap: Record<string, string> = {
          manual_binding: "blue",
          code_hint: "green",
          code_type: "green",
          code_type_generic: "cyan",
          name_heuristic: "orange",
        };
        return <Tag color={colorMap[s] ?? "default"}>{s}</Tag>;
      },
    },
    ...(editing
      ? [{
          title: "操作",
          width: 60,
          render: (_: unknown, row: EditableField) => (
            <Button
              type="text"
              danger
              size="small"
              icon={<DeleteOutlined />}
              onClick={() => handleDeleteField(row._key)}
            />
          ),
        }]
      : []),
  ];

  return (
    <div>
      {/* Schema description + purpose_detail */}
      {editing ? (
        <Space direction="vertical" style={{ width: "100%", marginBottom: 12 }}>
          <Input
            value={editDescription}
            onChange={(e) => setEditDescription(e.target.value)}
            maxLength={500}
            placeholder="表/集合描述..."
            addonBefore="描述"
          />
          <Input.TextArea
            value={editPurposeDetail}
            onChange={(e) => setEditPurposeDetail(e.target.value)}
            placeholder="用途详情..."
            rows={2}
            autoSize={{ minRows: 2, maxRows: 4 }}
          />
        </Space>
      ) : (
        <div style={{ marginBottom: 8 }}>
          {props.sco.description && (
            <div style={{ color: "#666" }}>{props.sco.description}</div>
          )}
          {props.sco.purpose_detail && (
            <div style={{ color: "#999", fontSize: 12, marginTop: 4 }}>{props.sco.purpose_detail}</div>
          )}
        </div>
      )}

      {/* Action buttons */}
      <div style={{ marginBottom: 8, display: "flex", justifyContent: "flex-end" }}>
        <Space>
          {editing ? (
            <>
              <Button
                size="small"
                type="primary"
                onClick={saveEdit}
                loading={saving}
                disabled={hasErrors}
              >
                保存
              </Button>
              <Button size="small" onClick={cancelEdit} disabled={saving}>
                取消
              </Button>
            </>
          ) : (
            <Button size="small" onClick={startEdit}>
              编辑 Schema
            </Button>
          )}
        </Space>
      </div>

      {/* Fields table */}
      <Table<EditableField>
        rowKey="_key"
        dataSource={dataSource}
        columns={columns}
        pagination={false}
        size="small"
        expandable={{
          rowExpandable: (r) => !!(r.sub_fields?.length || r.enum_values?.length || editing),
          expandedRowRender: (record) => {
            const idx = dataSource.findIndex((f) => f._key === record._key);
            return (
              <div>
                {(record.sub_fields && record.sub_fields.length > 0 || editing) && (
                  <SubFieldsTree
                    subFields={(record.sub_fields as EditableField[]) || []}
                    editing={editing}
                    validationErrors={validationErrors}
                    parentKey={`${record._key}_sub_`}
                    onSubFieldChange={(updatedSubs) => {
                      if (!editing) return;
                      handleSubFieldChange(idx, updatedSubs);
                    }}
                  />
                )}
                {record.enum_values && record.enum_values.length > 0 && (
                  <div style={{ padding: "8px 24px" }}>
                    <strong>枚举值: </strong>
                    {record.enum_values.map((v, i) => (
                      <Tag key={i}>
                        {v.db_value}
                        {v.name ? ` (${v.name})` : ""}
                        {v.description ? ` — ${v.description}` : ""}
                      </Tag>
                    ))}
                  </div>
                )}
              </div>
            );
          },
        }}
      />

      {/* ── Relationships section ── */}
      {props.sco.relationships && props.sco.relationships.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h4 style={{ marginBottom: 8 }}>关联关系 ({props.sco.relationships.length})</h4>
          <Table<SchemaCanonicalRelationship>
            rowKey={(r) => `${r.from_target}:${r.from_field}→${r.to_target}:${r.to_field}`}
            dataSource={props.sco.relationships}
            columns={[
              {
                title: "源表",
                dataIndex: "from_target",
                width: 180,
                render: (t: string) => <strong>{t}</strong>,
              },
              {
                title: "源字段",
                dataIndex: "from_field",
                width: 140,
              },
              {
                title: "类型",
                dataIndex: "relation_type",
                width: 100,
                render: (t: string) => <Tag>{t}</Tag>,
              },
              {
                title: "目标表",
                dataIndex: "to_target",
                width: 180,
                render: (t: string) => <strong>{t}</strong>,
              },
              {
                title: "目标字段",
                dataIndex: "to_field",
                width: 140,
              },
            ]}
            pagination={false}
            size="small"
          />
        </div>
      )}

      {/* Add field button */}
      {editing && (
        <Button
          type="dashed"
          icon={<PlusOutlined />}
          onClick={handleAddField}
          style={{ marginTop: 8, width: "100%" }}
        >
          添加字段
        </Button>
      )}

      {/* Enum Bind Drawer */}
      {bindDrawer && (
        <EnumBindDrawer
          open
          collectionId={props.sco.id}
          fieldName={getFieldName(bindDrawer.field)}
          fieldType={bindDrawer.field.type || ""}
          namespaceId={props.namespaceId}
          samples={bindDrawer.field.sample_values}
          onClose={() => setBindDrawer(null)}
          onBound={() => {
            setBindDrawer(null);
            props.onRefresh?.();
          }}
        />
      )}
    </div>
  );
}
