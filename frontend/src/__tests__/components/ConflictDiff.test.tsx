/* ════════════════════════════════════════════════════════════════════════════
 *  ConflictDiff — 空冲突 success Alert + 有冲突列表 + suggested label
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import ConflictDiff from "@/components/audit/ConflictDiff";

describe("ConflictDiff", () => {
  it("空冲突 → 渲染 success Alert", () => {
    render(<ConflictDiff conflicts={[]} />);
    expect(screen.getByText("未检测到冲突")).toBeInTheDocument();
  });

  it("undefined 容错 → success Alert", () => {
    render(<ConflictDiff conflicts={undefined as any} />);
    expect(screen.getByText("未检测到冲突")).toBeInTheDocument();
  });

  it("有冲突 → 渲染冲突列表 + suggested label 中文映射", () => {
    render(
      <ConflictDiff
        conflicts={[
          { existing_id: 99, reason: "完全重复", suggested: "merge" },
          { existing_id: 100, reason: "语义类似", suggested: "replace" },
          { existing_id: 101, reason: "范围不同", suggested: "coexist" },
          { existing_id: 102, reason: "未知动作", suggested: "unknown_op" },
        ]}
      />,
    );
    expect(screen.getByText(/检测到 4 条冲突/)).toBeInTheDocument();
    expect(screen.getByText("合并")).toBeInTheDocument();
    expect(screen.getByText("替换")).toBeInTheDocument();
    expect(screen.getByText("共存")).toBeInTheDocument();
    // unknown_op fallback to raw string
    expect(screen.getByText("unknown_op")).toBeInTheDocument();
    expect(screen.getByText("完全重复")).toBeInTheDocument();
  });
});
