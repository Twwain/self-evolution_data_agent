import { Button, Drawer, Form, Input, Space, Tag, message } from "antd";
import { useEffect, useState } from "react";

import { schemaCanonicalApi } from "@/api";
import type { EvidenceResponse } from "@/types/schema-canonical";
import { ConfidenceTag } from "./ConfidenceTag";

export function EvidenceDrawer(props: {
  namespaceId: number;
  scoId: number;
  fieldPath: string;
  open: boolean;
  onClose: () => void;
}) {
  const [data, setData] = useState<EvidenceResponse | null>(null);
  const [editing, setEditing] = useState(false);
  const [correctedValue, setCorrectedValue] = useState("");

  useEffect(() => {
    if (!props.open) return;
    void schemaCanonicalApi.getSchemaEvidence(props.namespaceId, props.scoId, props.fieldPath).then(setData);
  }, [props.open, props.namespaceId, props.scoId, props.fieldPath]);

  const submit = async (action: "confirm" | "correct" | "ignore") => {
    const body: { field_path: string; action: "confirm" | "correct" | "ignore"; corrected_value?: Record<string, unknown> } = {
      field_path: props.fieldPath,
      action,
    };
    if (action === "correct") {
      body.corrected_value = { description: correctedValue };
    }
    await schemaCanonicalApi.confirmField(props.namespaceId, props.scoId, body);
    message.success(`已${action === "confirm" ? "确认" : action === "correct" ? "修正" : "忽略"}`);
    props.onClose();
  };

  return (
    <Drawer
      title={`字段: ${props.fieldPath}`}
      open={props.open}
      onClose={props.onClose}
      width="40%"
    >
      {data && (
        <Space direction="vertical" style={{ width: "100%" }}>
          <div><strong>当前值 (Canonical):</strong> {data.canonical_value?.description ?? "—"}</div>

          {data.candidates.map((c) => (
            <div key={c.id} style={{ border: "1px solid #f0f0f0", padding: 12, borderRadius: 6 }}>
              <Space>
                <ConfidenceTag status={c.confidence_status} />
                <Tag>{c.status}</Tag>
              </Space>
              <div style={{ marginTop: 8 }}>
                <strong>{c.candidate_value.description ?? JSON.stringify(c.candidate_value)}</strong>
              </div>
              <ul style={{ marginTop: 8 }}>
                {c.evidence_sources.map((s, i) => (
                  <li key={i}>
                    <Tag color="blue">{s.source}</Tag>
                    {s.file ? `${s.file}${s.line ? `:${s.line}` : ""}` : ""}
                    {s.repo_url && <a href={s.repo_url} target="_blank" rel="noreferrer"> repo</a>}
                    {s.extra && <span style={{ color: "#888", fontSize: 12 }}> ({JSON.stringify(s.extra)})</span>}
                  </li>
                ))}
              </ul>
            </div>
          ))}

          {!editing ? (
            <Space>
              <Button type="primary" onClick={() => submit("confirm")}>确认</Button>
              <Button onClick={() => setEditing(true)}>修正</Button>
              <Button danger onClick={() => submit("ignore")}>忽略</Button>
            </Space>
          ) : (
            <Form layout="vertical">
              <Form.Item label="新值">
                <Input
                  value={correctedValue}
                  onChange={(e) => setCorrectedValue(e.target.value)}
                  aria-label="新值"
                />
              </Form.Item>
              <Space>
                <Button type="primary" onClick={() => submit("correct")}>提交修正</Button>
                <Button onClick={() => setEditing(false)}>取消</Button>
              </Space>
            </Form>
          )}
        </Space>
      )}
    </Drawer>
  );
}
