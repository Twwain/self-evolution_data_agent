/* ════════════════════════════════════════════════════════════════════════════
 *  ExampleEditPanel — example 类型 KE 编辑面板单测
 *  覆盖: 5 个字段渲染 / question 编辑回调 / result_summary 编辑 / query_json readOnly
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import ExampleEditPanel, { type ExamplePayload } from "@/components/audit/ExampleEditPanel";

const basePayload: ExamplePayload = {
  question: "统计品牌名称包含A级的品牌数量",
  target_collection: "c_brand",
  target_database: "db_rp_resources_20220305",
  query_json: { pipeline: [{ $match: { auditStatus: 0 } }] },
  result_summary: "在 c_brand 上按名称过滤统计数量",
};

describe("ExampleEditPanel", () => {
  it("渲染所有 5 个字段", () => {
    render(<ExampleEditPanel value={basePayload} onChange={() => {}} />);
    expect(screen.getByDisplayValue("统计品牌名称包含A级的品牌数量")).toBeInTheDocument();
    expect(screen.getByDisplayValue("在 c_brand 上按名称过滤统计数量")).toBeInTheDocument();
    expect(screen.getByText("c_brand")).toBeInTheDocument();
    expect(screen.getByText("db_rp_resources_20220305")).toBeInTheDocument();
    expect(screen.getByDisplayValue(/\$match/)).toBeInTheDocument();
  });

  it("question 可编辑触发 onChange", () => {
    const onChange = vi.fn();
    render(<ExampleEditPanel value={basePayload} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("统计品牌名称包含A级的品牌数量"), {
      target: { value: "新问题" },
    });
    expect(onChange).toHaveBeenCalledWith({
      ...basePayload,
      question: "新问题",
    });
  });

  it("result_summary 可编辑触发 onChange", () => {
    const onChange = vi.fn();
    render(<ExampleEditPanel value={basePayload} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("在 c_brand 上按名称过滤统计数量"), {
      target: { value: "新摘要" },
    });
    expect(onChange).toHaveBeenCalledWith({
      ...basePayload,
      result_summary: "新摘要",
    });
  });

  it("query_json 视图为 readOnly", () => {
    render(<ExampleEditPanel value={basePayload} onChange={() => {}} />);
    const ta = screen.getByDisplayValue(/\$match/);
    expect(ta).toHaveAttribute("readonly");
  });
});
