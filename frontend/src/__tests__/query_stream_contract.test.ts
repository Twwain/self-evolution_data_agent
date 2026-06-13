import { describe, it, expect, vi, beforeEach } from "vitest";

import { openAgentStream } from "@/api/sseClient";

// L1 契约: 断言前端发出的请求 URL+method+body 形状 (mock 全局 fetch, 不 mock api 模块).
describe("query/stream 请求契约 (L1)", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("POST /api/query/stream, body 含 namespace_id + question", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        'event: final_answer\ndata: {"content":"ok","history_id":1}\n\n',
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { done } = await openAgentStream({
      url: "/api/query/stream",
      body: { namespace_id: 1, question: "按月统计订单额" },
      onEvent: () => {},
    });
    await done;

    expect(fetchMock).toHaveBeenCalled();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(String(url)).toContain("/api/query/stream");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body));
    expect(body).toHaveProperty("namespace_id", 1);
    expect(body).toHaveProperty("question", "按月统计订单额");
  });
});
