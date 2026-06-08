/* ════════════════════════════════════════════
 *  RelatedEntriesPanel — 展示 A-MEM 入库时 LLM 检测的关联条目
 *
 *  Stage 2 抓手 D: approve 后驱动合并/补充/覆盖.
 * ════════════════════════════════════════════ */

import { useState } from "react";
import { Button, Empty, Tag } from "antd";
import { RelatedEntryDetailModal } from "./RelatedEntryDetailModal";

interface Related {
  related_entry_id: number;
  relation: "equivalent" | "supplement" | "conflict";
  llm_reason: string;
  detected_at: string;
}

const RELATION_META: Record<string, { color: string; icon: string; label: string }> = {
  equivalent: { color: "blue", icon: "≡", label: "等价" },
  supplement: { color: "green", icon: "+", label: "补充" },
  conflict: { color: "red", icon: "⚠", label: "冲突" },
};

interface Props {
  related_entry_ids_json: string;
}

export function RelatedEntriesPanel({ related_entry_ids_json }: Props) {
  const [detailId, setDetailId] = useState<number | null>(null);
  let parsed: Related[] = [];
  try {
    parsed = JSON.parse(related_entry_ids_json || "[]");
  } catch {
    parsed = [];
  }
  if (parsed.length === 0) return <Empty description="无关联条目" />;
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ marginBottom: 8, fontSize: 12, color: "#999" }}>
        以下为入库时 LLM 自动检测的关联条目. approve 后将驱动合并/补充/覆盖.
      </div>
      {parsed.map((r) => {
        const meta = RELATION_META[r.relation] ?? { color: "default", icon: "?", label: r.relation };
        return (
          <div key={r.related_entry_id} style={{ marginBottom: 6 }}>
            <Tag color={meta.color}>{meta.icon} {meta.label}</Tag>
            <span>条目 #{r.related_entry_id}: {r.llm_reason}</span>
            <Button size="small" type="link" onClick={() => setDetailId(r.related_entry_id)}>
              查看详情
            </Button>
          </div>
        );
      })}
      {detailId !== null && (
        <RelatedEntryDetailModal
          entryId={detailId}
          onClose={() => setDetailId(null)}
        />
      )}
    </div>
  );
}
