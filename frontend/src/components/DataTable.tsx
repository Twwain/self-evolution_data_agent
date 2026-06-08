/* ════════════════════════════════════════════
 *  数据表格 — Ant Design Table
 * ════════════════════════════════════════════ */

import React from "react";
import { Table } from "antd";
import { normalizeRows } from "@/utils/normalizeRows";

interface Props {
  columns: string[];
  rows: unknown[];
}

const DataTable: React.FC<Props> = ({ columns, rows }) => {
  const normalized = normalizeRows(rows, columns);
  const tableColumns = columns.map((col) => ({
    title: col,
    dataIndex: col,
    key: col,
    ellipsis: true,
  }));

  const dataSource = normalized.map((row, i) => {
    const record: Record<string, any> = { key: i };
    columns.forEach((col, j) => {
      record[col] = row[j];
    });
    return record;
  });

  return (
    <Table
      columns={tableColumns}
      dataSource={dataSource}
      size="small"
      scroll={{ x: true }}
      pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
    />
  );
};

export default DataTable;
