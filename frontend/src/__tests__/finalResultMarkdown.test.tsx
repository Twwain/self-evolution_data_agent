// 验证 FinalResult 把 markdown 渲染成真实 DOM（表格/标题/加粗），而非纯文本
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { FinalResult } from "../components/stream/FinalResult";

const MD = `## 一、实发电量总览

| 电站 | 实发电量总和 |
|:----:|:----------:|
| **D电站** | **242,697,596** |
| **G电站** | **87,586,410** |

> D电站约为G电站的 **2.77倍**。

- 要点一
- 要点二`;

describe("FinalResult markdown rendering", () => {
  it("renders markdown content as real DOM, not raw text", () => {
    const { container } = render(<FinalResult content={MD} />);

    // 标题被解析为 <h2>
    const h2 = container.querySelector("h2");
    expect(h2?.textContent).toContain("实发电量总览");

    // GFM 表格被解析为 <table>，含表头与单元格
    const table = container.querySelector("table");
    expect(table).not.toBeNull();
    expect(container.querySelectorAll("th").length).toBe(2);
    expect(container.querySelectorAll("td").length).toBe(4);

    // 加粗解析为 <strong>
    expect(container.querySelector("strong")).not.toBeNull();

    // 引用块与列表
    expect(container.querySelector("blockquote")).not.toBeNull();
    expect(container.querySelectorAll("li").length).toBe(2);

    // 关键：原始 markdown 记号不应作为可见文本残留
    expect(container.textContent).not.toContain("|:----:|");
    expect(container.textContent).not.toContain("## ");
  });
});
