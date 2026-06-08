import { Tag } from "antd";
import type { ConfidenceStatus } from "@/types/schema-canonical";

const STYLES: Record<ConfidenceStatus, { color: string; label: string }> = {
  confirmed_by_introspect: { color: "blue", label: "DBA 注释" },
  confirmed_by_code: { color: "green", label: "代码确认" },
  confirmed_by_user: { color: "default", label: "已确认" },
  evidence_only: { color: "orange", label: "需人工确认" },
  unverified: { color: "", label: "" },
};

export function ConfidenceTag({ status }: { status: ConfidenceStatus }) {
  const style = STYLES[status];
  if (!style.label) return null;
  return <Tag color={style.color}>{style.label}</Tag>;
}
