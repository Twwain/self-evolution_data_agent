import React from "react";
import { Tag, Card } from "antd";

// ============================================================================
// KnowledgeProposedToast — agent 自动学习的待审核知识条目卡片
// items 空时不渲染, 避免空白噪音
// ============================================================================
interface Item {
  entryId: number;
  entryType: string;
  preview: string;
}

export const KnowledgeProposedToast: React.FC<{ items: Item[] }> = ({ items }) => {
  if (items.length === 0) return null;
  return (
    <Card size="small" title="📚 学到新知识 (待审核)" style={{ marginTop: 8 }}>
      {items.map((it) => (
        <div key={it.entryId} style={{ marginBottom: 4 }}>
          <Tag color="blue">{it.entryType}</Tag>
          <span>{it.preview}</span>
        </div>
      ))}
    </Card>
  );
};
