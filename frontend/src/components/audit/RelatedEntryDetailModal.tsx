/* ════════════════════════════════════════════
 *  RelatedEntryDetailModal — 查看关联条目详情
 *
 *  Stage 2 抓手 D: 审核 UI 点击"查看详情"弹窗.
 * ════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { Descriptions, Modal, Spin } from "antd";
import { getKnowledgeEntry } from "@/api";
import type { KnowledgeEntry } from "@/types";

interface Props {
  entryId: number;
  onClose: () => void;
}

export function RelatedEntryDetailModal({ entryId, onClose }: Props) {
  const [entry, setEntry] = useState<KnowledgeEntry | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    getKnowledgeEntry(entryId)
      .then(setEntry)
      .catch(() => setEntry(null))
      .finally(() => setLoading(false));
  }, [entryId]);

  return (
    <Modal open onCancel={onClose} footer={null} title={`知识条目 #${entryId}`} width={720}>
      {loading ? (
        <Spin />
      ) : !entry ? (
        <div>条目不存在或已删除</div>
      ) : (
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="entry_type">{entry.entry_type}</Descriptions.Item>
          <Descriptions.Item label="status">{entry.status}</Descriptions.Item>
          <Descriptions.Item label="content">{entry.content}</Descriptions.Item>
          <Descriptions.Item label="source">{entry.source}</Descriptions.Item>
          <Descriptions.Item label="tier">{entry.tier}</Descriptions.Item>
        </Descriptions>
      )}
    </Modal>
  );
}
