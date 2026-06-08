/* ════════════════════════════════════════════
 *  ConflictDiff — 冲突对照展示
 * ════════════════════════════════════════════ */

import React from "react";
import { Alert, Card, List, Tag } from "antd";

interface ConflictItem {
  existing_id: number;
  reason: string;
  suggested: string;
}

const SUGGESTED_LABEL: Record<string, string> = {
  merge: "合并", replace: "替换", coexist: "共存",
};

export default function ConflictDiff({ conflicts }: { conflicts: ConflictItem[] }) {
  if (!conflicts?.length) {
    return <Alert type="success" showIcon message="未检测到冲突" />;
  }
  return (
    <Card size="small" title={`检测到 ${conflicts.length} 条冲突`}>
      <List
        dataSource={conflicts}
        renderItem={(c) => (
          <List.Item>
            <List.Item.Meta
              title={
                <span>
                  与 #{c.existing_id} 冲突 <Tag>{SUGGESTED_LABEL[c.suggested] ?? c.suggested}</Tag>
                </span>
              }
              description={c.reason}
            />
          </List.Item>
        )}
      />
    </Card>
  );
}
