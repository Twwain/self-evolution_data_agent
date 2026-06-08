/* ════════════════════════════════════════════
 *  BatchProgress — 批量审核进度条 + 失败计数
 * ════════════════════════════════════════════ */

import React from "react";
import { Progress, Tag } from "antd";

interface Props {
  total: number;
  done: number;
  failedIds: number[];
}

export default function BatchProgress({ total, done, failedIds }: Props) {
  if (!total) return null;
  const percent = Math.round((done / total) * 100);
  return (
    <div style={{ marginTop: 8 }}>
      <Progress percent={percent} size="small" />
      <span style={{ marginLeft: 8 }}>
        {done}/{total}
      </span>
      {failedIds.length > 0 && (
        <Tag color="red" style={{ marginLeft: 8 }}>
          failed: {failedIds.length}
        </Tag>
      )}
    </div>
  );
}
