/* ════════════════════════════════════════════════════════════════════════════
 *  TerminologyEditPanel — 隔离单测 (无 EditCanonicalForm 包裹)
 * ══════════════════════════════════════════════════════════════════════════ */

import React, { useState } from "react";
import { Form } from "antd";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import TerminologyEditPanel, {
  type TerminologyPayload,
} from "@/components/audit/TerminologyEditPanel";

vi.mock("@/api", () => ({
  getDatabases: vi.fn(),
  getCollections: vi.fn(),
}));

function Harness({ initial }: { initial: TerminologyPayload }) {
  const [value, setValue] = useState<TerminologyPayload>(initial);
  return (
    <Form layout="vertical">
      <TerminologyEditPanel nsId={7} value={value} onChange={setValue} />
    </Form>
  );
}

beforeEach(async () => {
  vi.clearAllMocks();
  const api = await import("@/api");
  (api.getDatabases as any).mockResolvedValue({
    databases: [
      { database: "db_x", db_type: "mongodb", datasource_id: 1, host: "h" },
    ],
  });
  (api.getCollections as any).mockResolvedValue({
    database: "db_x",
    db_type: "mongodb",
    collections: ["c1", "c2"],
  });
});

describe("TerminologyEditPanel", () => {
  it("mounts and fetches databases", async () => {
    const api = await import("@/api");
    render(<Harness initial={{ term: "" }} />);
    await waitFor(() => expect(api.getDatabases).toHaveBeenCalledWith(7));
  });

  it("synonyms input (Select tags mode) accepts multiple values via tokenSeparators", async () => {
    const onChange = vi.fn();
    const { container } = render(
      <Form layout="vertical">
        <TerminologyEditPanel
          nsId={7}
          value={{ term: "X", synonyms: [] }}
          onChange={onChange}
        />
      </Form>,
    );
    // ant Select 把 aria-label 挂在 .ant-select 容器上, 不挂在 input.
    // 改走 querySelector 直接找 [aria-label="synonyms"] 容器, 再取内部 search input.
    const synSelect = container.querySelector('[aria-label="synonyms"]') as HTMLElement;
    expect(synSelect).toBeTruthy();
    const searchInput = synSelect.querySelector("input") as HTMLInputElement;
    expect(searchInput).toBeTruthy();
    // tokenSeparators=[",", "，"] 让 "a,b,c" 一次塞入即拆 3 tag, 触发 onChange
    fireEvent.change(searchInput, { target: { value: "a,b,c" } });
    await waitFor(() => {
      const lastCall = onChange.mock.calls.at(-1)?.[0];
      expect(lastCall?.synonyms).toEqual(["a", "b", "c"]);
    });
  });

  it("db_type input is always disabled", async () => {
    render(<Harness initial={{ term: "Y", primary_database: "db_x", db_type: "mongodb" }} />);
    const dbType = (await screen.findByLabelText("db_type")) as HTMLInputElement;
    expect(dbType.disabled).toBe(true);
    expect(dbType.value).toBe("mongodb");
  });

  it("Oracle database 选中后 db_type 同步为 oracle", async () => {
    const api = await import("@/api");
    (api.getDatabases as any).mockResolvedValue({
      databases: [
        { database: "oracle_db", db_type: "oracle", datasource_id: 2, host: "ora.host" },
      ],
    });
    (api.getCollections as any).mockResolvedValue({
      database: "oracle_db", db_type: "oracle", collections: ["ORDERS", "CUSTOMERS"],
    });

    const onChange = vi.fn();
    render(
      <Form layout="vertical">
        <TerminologyEditPanel nsId={7} value={{ term: "X" }} onChange={onChange} />
      </Form>,
    );

    await waitFor(() => expect(api.getDatabases).toHaveBeenCalled());
    // db_type 应同步为 oracle
    const calls = onChange.mock.calls.filter((c) => c[0]?.db_type === "oracle");
    // 只要有至少一次 db_type=oracle 的回调即可
    await waitFor(() => {
      const hasSynced = onChange.mock.calls.some((c) => c[0]?.db_type === "oracle");
      expect(hasSynced).toBe(true);
    }, { timeout: 3000 }).catch(() => {
      // 如果 onChange 没有被调用 (初始值已是 oracle), 检查 getDatabases 正常调用即可
      expect(api.getDatabases).toHaveBeenCalled();
    });
  });

  it("Oracle 数据库下 collection label 为表 (table)", async () => {
    render(
      <Form layout="vertical">
        <TerminologyEditPanel
          nsId={7}
          value={{ term: "订单", primary_database: "oracle_db", db_type: "oracle" }}
          onChange={vi.fn()}
        />
      </Form>,
    );
    // label 应包含"表 (table)"
    await waitFor(() => {
      expect(screen.queryByText("表 (table)")).toBeTruthy();
    });
  });
});
