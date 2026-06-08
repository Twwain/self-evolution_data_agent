/* ════════════════════════════════════════════════════════════════════════════
 *  TerminologyConflictModal — Phase 3 Task 3.3 + manual_edit 扩展
 *
 *  5 resolution_choice (keep_existing / replace / merge_both / reject_both /
 *  manual_edit), 并排展示 existing vs candidate 完整 payload, 点 manual_edit
 *  展开 TerminologyEditPanel 锁定路由让用户改 term+synonyms 后保存.
 *
 *  契约: onClose({ resolved: bool, choice?: 5 选 1 })
 *  - 解决成功 → resolved=true + choice
 *  - 用户取消 / 失败 → resolved=false
 * ══════════════════════════════════════════════════════════════════════════ */

import React, { useMemo, useState } from "react";
import { Alert, Modal, Button, Form, Space, Tag, Typography, Card, message } from "antd";
import { resolveTerminologyConflict } from "@/api";
import type { TerminologyConflict } from "@/types";
import TerminologyEditPanel, { type TerminologyPayload } from "./TerminologyEditPanel";

type Choice = "keep_existing" | "replace" | "merge_both" | "reject_both" | "manual_edit";

interface TermPayload {
  term?: string;
  synonyms?: string[];
  primary_database?: string;
  primary_collection?: string;
  db_type?: string;
  source_collections?: string[];
}

interface Props {
  conflict: TerminologyConflict;
  /** existing 卡片内容. 优先用 conflict.existing_payload (后端注入), 兼容旧调用方传 existing 简化对象. */
  existing?: TermPayload;
  open: boolean;
  onClose: (result: { resolved: boolean; choice?: Choice }) => void;
}

const CHOICES: ReadonlyArray<{
  v: Choice;
  label: string;
  desc: string;
  primary?: boolean;
  danger?: boolean;
}> = [
  { v: "keep_existing", label: "保留现有", desc: "候选丢弃, 现有不变" },
  { v: "replace", label: "用候选替换", desc: "现有标 superseded, 新建 proposed" },
  { v: "merge_both", label: "合并同义词", desc: "现有 ∪ 候选 (term + synonyms)", primary: true },
  { v: "manual_edit", label: "手动编辑", desc: "就地改 existing → canonical (路由锁定)" },
  { v: "reject_both", label: "都拒绝", desc: "现有标 rejected, 候选丢弃", danger: true },
];

// ── 子卡片: 渲染单条 term 的完整路由 + 同义词信息 ──────────────────
function TermInfoCard({
  title,
  payload,
  fallbackId,
  testId,
}: {
  title: string;
  payload: TermPayload;
  fallbackId?: number;
  testId: string;
}) {
  const synonyms = payload.synonyms ?? [];
  const sourceColls = payload.source_collections ?? [];
  return (
    <Card size="small" title={title} style={{ flex: 1 }} data-testid={testId}>
      <Typography.Text strong style={{ fontSize: 16 }}>
        {payload.term ?? (fallbackId !== undefined ? `#${fallbackId}` : "—")}
      </Typography.Text>
      <div style={{ marginTop: 8 }}>
        <Space size={4} wrap>
          {payload.db_type && (
            <Tag color={payload.db_type === "mongodb" ? "geekblue" : "purple"}>
              {payload.db_type}
            </Tag>
          )}
          {payload.primary_database && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              database: <Typography.Text code>{payload.primary_database}</Typography.Text>
            </Typography.Text>
          )}
        </Space>
      </div>
      {payload.primary_collection && (
        <div style={{ marginTop: 4, fontSize: 12 }}>
          <Typography.Text type="secondary">collection: </Typography.Text>
          <Typography.Text code>{payload.primary_collection}</Typography.Text>
        </div>
      )}
      <div style={{ marginTop: 6, fontSize: 12 }}>
        <Typography.Text type="secondary">同义词: </Typography.Text>
        {synonyms.length > 0 ? synonyms.map((s) => <Tag key={s}>{s}</Tag>) : "—"}
      </div>
      {sourceColls.length > 1 && (
        <div style={{ marginTop: 4, fontSize: 12 }}>
          <Typography.Text type="secondary">关联 collections: </Typography.Text>
          {sourceColls.map((c) => <Tag key={c}>{c}</Tag>)}
        </div>
      )}
    </Card>
  );
}

const TerminologyConflictModal: React.FC<Props> = ({
  conflict,
  existing,
  open,
  onClose,
}) => {
  const [busy, setBusy] = useState<Choice | null>(null);
  const [editMode, setEditMode] = useState(false);

  // existing 优先级: prop > conflict.existing_payload > {} 兜底
  const existingPayload: TermPayload = (existing
    ?? (conflict.existing_payload as TermPayload | undefined)
    ?? {});

  const candidate: TermPayload = useMemo(() => {
    try {
      return JSON.parse(conflict.candidate_payload) as TermPayload;
    } catch {
      return {};
    }
  }, [conflict.candidate_payload]);

  // ── manual_edit 表单初始 payload: existing 兜底 + synonyms 取 existing ∪ candidate 并集 ──
  const initialEditedPayload = useMemo<TerminologyPayload>(() => {
    const mergedSynonyms = Array.from(new Set([
      ...(existingPayload.synonyms ?? []),
      candidate.term ?? "",
      ...(candidate.synonyms ?? []),
    ].filter((s): s is string => Boolean(s) && s !== existingPayload.term)));
    const mergedSourceColls = Array.from(new Set([
      ...(existingPayload.source_collections ?? []),
      ...(candidate.source_collections ?? []),
    ]));
    return {
      term: existingPayload.term ?? "",
      primary_database: existingPayload.primary_database,
      primary_collection: existingPayload.primary_collection,
      db_type: existingPayload.db_type as "mysql" | "mongodb" | undefined,
      synonyms: mergedSynonyms,
      source_collections: mergedSourceColls,
    };
  }, [existingPayload, candidate]);

  const [editedPayload, setEditedPayload] = useState<TerminologyPayload>(initialEditedPayload);
  // initialEditedPayload 变化(切换不同 conflict 时)同步重置
  React.useEffect(() => {
    setEditedPayload(initialEditedPayload);
  }, [initialEditedPayload]);

  // ── 冲突原因诊断: 同 (db, collection, db_type) 三元组撞了 ──
  const sameRouting =
    !!existingPayload.primary_database &&
    !!existingPayload.primary_collection &&
    existingPayload.primary_database === candidate.primary_database &&
    existingPayload.primary_collection === candidate.primary_collection &&
    existingPayload.db_type === candidate.db_type;

  const conflictReason = sameRouting
    ? `两条术语指向同一数据表 (${candidate.db_type ?? "?"} · ${candidate.primary_database ?? "?"} · ${candidate.primary_collection ?? "?"}), 但 term 与同义词集合互不相交 — 系统认为是两个不同概念抢占同一张表, 等你裁决.`
    : "现有与候选的路由信息不一致, 这通常不该发生 — 请优先「保留现有」并复查 extractor 输出.";

  const handleResolve = async (choice: Choice) => {
    if (choice === "manual_edit") {
      // 切到内嵌编辑模式, 不立即调 API
      setEditMode(true);
      return;
    }
    setBusy(choice);
    try {
      await resolveTerminologyConflict(conflict.namespace_id, conflict.id, choice);
      message.success(`已解决 (${choice})`);
      onClose({ resolved: true, choice });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? "解决冲突失败");
    } finally {
      setBusy(null);
    }
  };

  const handleSubmitManualEdit = async () => {
    // 简单前端校验 — 后端会 422 兜底, 但前端拦掉显式空 term 体验更好
    if (!editedPayload.term?.trim()) {
      message.warning("term 不能为空");
      return;
    }
    setBusy("manual_edit");
    try {
      await resolveTerminologyConflict(
        conflict.namespace_id, conflict.id, "manual_edit",
        editedPayload as Record<string, unknown>,
      );
      message.success("已手动编辑并通过");
      onClose({ resolved: true, choice: "manual_edit" });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? "保存失败");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal
      title={`术语冲突 #${conflict.id}`}
      open={open}
      footer={null}
      onCancel={() => onClose({ resolved: false })}
      width={860}
      destroyOnHidden
    >
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {/* ── 冲突原因解释 ── */}
        <Alert
          type={sameRouting ? "warning" : "error"}
          showIcon
          message="为什么是冲突?"
          description={conflictReason}
        />

        {/* ── 并排 diff 卡片 ── */}
        <Space size="middle" style={{ display: "flex", width: "100%" }} align="start">
          <TermInfoCard
            title="已有 (existing)"
            payload={existingPayload}
            fallbackId={conflict.existing_entry_id}
            testId="existing-card"
          />
          <TermInfoCard
            title={`候选 (来源 ${conflict.candidate_source})`}
            payload={candidate}
            testId="candidate-card"
          />
        </Space>

        {/* ── 4 + 1 choice 按钮 ── */}
        {!editMode && (
          <Space wrap>
            {CHOICES.map((c) => (
              <Button
                key={c.v}
                type={c.primary ? "primary" : "default"}
                danger={c.danger}
                loading={busy === c.v}
                disabled={busy !== null && busy !== c.v}
                onClick={() => handleResolve(c.v)}
                aria-label={`choice-${c.v}`}
                title={c.desc}
              >
                {c.label}
              </Button>
            ))}
          </Space>
        )}

        {/* ── manual_edit 内嵌表单 ── */}
        {editMode && (
          <Card size="small" title="手动编辑 (路由三元组锁定)" data-testid="manual-edit-form">
            <Form layout="vertical">
              <TerminologyEditPanel
                nsId={conflict.namespace_id}
                value={editedPayload}
                onChange={setEditedPayload}
                lockRouting
              />
              <Space>
                <Button
                  type="primary"
                  loading={busy === "manual_edit"}
                  onClick={handleSubmitManualEdit}
                  aria-label="manual-edit-save"
                >
                  保存并通过
                </Button>
                <Button
                  onClick={() => setEditMode(false)}
                  disabled={busy === "manual_edit"}
                  aria-label="manual-edit-cancel"
                >
                  返回
                </Button>
              </Space>
            </Form>
          </Card>
        )}
      </Space>
    </Modal>
  );
};

export default TerminologyConflictModal;
