/* ════════════════════════════════════════════
 *  BatchAuditBar — 批量审核条 (含 confirm_token 二次确认 + 进度)
 * ════════════════════════════════════════════ */

import React, { useState } from "react";
import { Alert, Button, Modal, Space, Spin, message } from "antd";
import { batchAudit } from "@/api";
import BatchProgress from "./BatchProgress";

interface Props {
  entryIds: number[];
  onDone?: () => void;
}

interface ProgressState {
  total: number;
  done: number;
  failedIds: number[];
}

export default function BatchAuditBar({ entryIds, onDone }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState<ProgressState>({ total: 0, done: 0, failedIds: [] });

  const runBatch = async (
    actions: Array<{ entry_id: number; action: "approve" | "reject"; reason?: string }>,
    actionLabel: string,
    confirmToken?: string,
  ) => {
    setProgress({ total: actions.length, done: 0, failedIds: [] });
    try {
      const r = await batchAudit(actions, confirmToken);
      setProgress((p) => ({ ...p, done: actions.length }));
      message.success(`已批量${actionLabel} ${r.affected_count} 条`);
      onDone?.();
    } catch (err) {
      setProgress((p) => ({
        ...p,
        done: actions.length,
        failedIds: actions.map((a) => a.entry_id),
      }));
      throw err;
    }
  };

  const exec = async (action: "approve" | "reject") => {
    const reason = action === "reject" ? window.prompt("批量拒绝原因 (必填)") : "";
    if (action === "reject" && !reason?.trim()) return;

    const actions = entryIds.map((id) => ({
      entry_id: id, action,
      ...(action === "reject" ? { reason: reason || "" } : {}),
    }));
    const label = action === "approve" ? "通过" : "拒绝";

    setSubmitting(true);
    try {
      try {
        await runBatch(actions, label);
      } catch (e: any) {
        // 422 confirm_token_required → 二次确认
        const detail = e?.response?.data?.detail;
        if (detail?.error === "confirm_token_required") {
          Modal.confirm({
            title: "二次确认",
            content: `操作影响 ${detail.affected_count} 条 (超阈值), 确认继续吗?`,
            onOk: async () => {
              await runBatch(actions, label, detail.expected_token);
            },
          });
        } else {
          throw e;
        }
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? "批量操作失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Alert
      style={{ marginBottom: 16 }}
      message={
        <div>
          <Space>
            <span>已选 {entryIds.length} 条</span>
            <Spin spinning={submitting} size="small">
              <Button type="primary" size="small" onClick={() => exec("approve")}>批量通过</Button>
              <Button size="small" danger onClick={() => exec("reject")} style={{ marginLeft: 8 }}>批量拒绝</Button>
            </Spin>
          </Space>
          <BatchProgress {...progress} />
        </div>
      }
    />
  );
}
