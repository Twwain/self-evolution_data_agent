import { Button, Card, Form, Input, Space } from "antd";
import { useState } from "react";

import type { SchemaConflict } from "@/types/schema-canonical";

export type ResolveBody =
  | { resolution_choice: "keep_a" | "keep_b" | "reject_all"; candidate_id?: number; reason?: string }
  | { resolution_choice: "merge"; resolution_value: Record<string, unknown>; reason?: string };

const LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

export function ConflictResolver(props: {
  conflict: SchemaConflict;
  onResolve: (body: ResolveBody) => void;
}) {
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [editValue, setEditValue] = useState<string>("");
  const [editing, setEditing] = useState<boolean>(false);

  const c = props.conflict;
  const isSemantic = c.conflict_type === "semantic_equivalent";
  const isMultiCandidate = !isSemantic && c.candidates_snapshot.length >= 2;

  // Negative-path: check if editValue is empty/whitespace-only
  const isEditValueEmpty = editing && editValue.trim() === "";

  // JSON validation for edit mode
  const jsonError = (() => {
    if (!editing || editValue.trim() === "") return null;
    try {
      JSON.parse(editValue);
      return null;
    } catch {
      return "输入内容不是有效的 JSON 格式";
    }
  })();

  const isSubmitDisabled = selectedIndex === null || isEditValueEmpty || jsonError !== null;

  const handleCardClick = (index: number) => {
    if (index === selectedIndex) return;
    setSelectedIndex(index);
    const cand = c.candidates_snapshot[index];
    setEditValue(JSON.stringify(cand.value, null, 2));
    setEditing(false);
  };

  const handleEditClick = () => {
    setEditing(!editing);
  };

  const handleMultiSubmit = () => {
    if (selectedIndex === null) return;
    if (editing) {
      // editValue is already validated JSON — parse it directly as resolution_value
      props.onResolve({
        resolution_choice: "merge",
        resolution_value: JSON.parse(editValue),
      });
    } else {
      // Send candidate_id so backend knows which candidate to keep
      const candidateId = c.candidates_snapshot[selectedIndex].candidate_id;
      props.onResolve({
        resolution_choice: "keep_a",
        candidate_id: candidateId,
        reason: `selected candidate ${LABELS[selectedIndex]}`,
      });
    }
  };

  // Unified card renderer for both multi-candidate and 2-candidate modes
  const renderCandidateCards = () => (
    <Space align="start" wrap>
      {c.candidates_snapshot.map((cand, i) => {
        const label = isMultiCandidate ? LABELS[i] : i === 0 ? "A" : "B";
        const isSelected = isMultiCandidate && selectedIndex === i;
        return (
          <Card
            key={i}
            size="small"
            title={`${label}: ${cand.source ?? `候选 ${i + 1}`}`}
            style={{
              minWidth: 220,
              cursor: isMultiCandidate ? "pointer" : undefined,
              border: isSelected ? "2px solid #1677ff" : undefined,
            }}
            onClick={isMultiCandidate ? () => handleCardClick(i) : undefined}
          >
            <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
              {JSON.stringify(cand.value, null, 2)}
            </pre>
            {isSelected && (
              <Button
                size="small"
                style={{ marginTop: 8 }}
                onClick={(e) => {
                  e.stopPropagation();
                  handleEditClick();
                }}
              >
                编辑
              </Button>
            )}
          </Card>
        );
      })}
    </Space>
  );

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {renderCandidateCards()}

      {isMultiCandidate ? (
        <>
          {editing && selectedIndex !== null && (
            <Form layout="vertical">
              <Form.Item
                label="编辑候选值"
                validateStatus={jsonError ? "error" : undefined}
                help={jsonError}
              >
                <Input.TextArea
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  rows={4}
                  aria-label="编辑候选值"
                />
              </Form.Item>
            </Form>
          )}

          <Space>
            <Button
              type="primary"
              disabled={isSubmitDisabled}
              onClick={handleMultiSubmit}
            >
              提交选择
            </Button>
            <Button danger onClick={() => props.onResolve({ resolution_choice: "reject_all" })}>
              全部拒绝
            </Button>
          </Space>
        </>
      ) : (
        <>
          <Button
            type="primary"
            onClick={() =>
              props.onResolve({
                resolution_choice: "merge",
                resolution_value: c.candidates_snapshot[0].value as Record<string, unknown>,
              })
            }
          >
            确认等价
          </Button>
        </>
      )}
    </Space>
  );
}
