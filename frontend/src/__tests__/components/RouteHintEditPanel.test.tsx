/* ════════════════════════════════════════════════════════════════════════════
 *  RouteHintEditPanel — route_hint 类型 KE 编辑面板单测
 *  覆盖: 4 字段渲染 / reason 编辑回调 + maxLength=30 / 空 join_fields 渲染 '无'
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import RouteHintEditPanel, { type RouteHintPayload } from "@/components/audit/RouteHintEditPanel";

const basePayload: RouteHintPayload = {
  collection_path: ["c_product", "c_category_group"],
  join_fields: [{ a: "c_product.categoryId", b: "c_category_group._id" }],
  cost_strategy: "default",
  reason: "商品→订单两层关联",
};

describe("RouteHintEditPanel", () => {
  it("渲染 collection_path / join / strategy / reason", () => {
    render(<RouteHintEditPanel value={basePayload} onChange={() => {}} />);
    expect(screen.getByText("c_product")).toBeInTheDocument();
    expect(screen.getByText("c_category_group")).toBeInTheDocument();
    expect(screen.getByText("c_product.categoryId ↔ c_category_group._id")).toBeInTheDocument();
    expect(screen.getByText("default")).toBeInTheDocument();
    expect(screen.getByDisplayValue("商品→订单两层关联")).toBeInTheDocument();
  });

  it("reason 可编辑且 maxLength=30", () => {
    const onChange = vi.fn();
    render(<RouteHintEditPanel value={basePayload} onChange={onChange} />);
    const input = screen.getByDisplayValue("商品→订单两层关联");
    fireEvent.change(input, { target: { value: "新理由" } });
    expect(onChange).toHaveBeenCalledWith({ ...basePayload, reason: "新理由" });
    expect(input).toHaveAttribute("maxlength", "30");
  });

  it("空 join_fields 渲染 '无'", () => {
    render(
      <RouteHintEditPanel
        value={{ ...basePayload, join_fields: [] }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("无")).toBeInTheDocument();
  });
});
