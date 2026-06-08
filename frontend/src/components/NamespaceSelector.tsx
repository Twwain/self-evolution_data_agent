/* ════════════════════════════════════════════
 *  命名空间选择器
 *  行为: list 加载后, 若外部 value 未定义, 按 localStorage → list[0]
 *        顺序自动选中并回调, 并同步写入 localStorage.
 * ════════════════════════════════════════════ */

import React, { useEffect, useState } from "react";
import { Select } from "antd";
import { fetchNamespaces } from "@/api";
import { readLastNamespaceId, writeLastNamespaceId } from "@/hooks/useLastNamespaceId";
import type { Namespace } from "@/types";

interface Props {
  value?: number;
  onChange: (id: number, ns: Namespace) => void;
  style?: React.CSSProperties;
}

const NamespaceSelector: React.FC<Props> = ({ value, onChange, style }) => {
  const [list, setList] = useState<Namespace[]>([]);

  useEffect(() => {
    fetchNamespaces().then((nss) => {
      setList(nss);
      if (value === undefined && nss.length > 0) {
        const remembered = readLastNamespaceId();
        const defaultNs = nss.find((n) => n.id === remembered) ?? nss[0];
        writeLastNamespaceId(defaultNs.id);
        onChange(defaultNs.id, defaultNs);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Select
      placeholder="选择命名空间"
      value={value}
      onChange={(id) => {
        const ns = list.find((n) => n.id === id);
        if (ns) {
          writeLastNamespaceId(id);
          onChange(id, ns);
        }
      }}
      style={{ width: 220, ...style }}
      options={list.map((n) => ({ value: n.id, label: n.name }))}
    />
  );
};

export default NamespaceSelector;
