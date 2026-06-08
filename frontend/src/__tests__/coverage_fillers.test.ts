/* ════════════════════════════════════════════════════════════════════════════
 *  Stage 6 收尾补丁: 补 sse types / api correction / hooks useAgentStream 缺口
 * ══════════════════════════════════════════════════════════════════════════ */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { isToolResult, isClarify } from "@/types/sse";
import {
  cancelStream,
  fetchActiveWorkers,
  fetchStreamStatus,
  submitCorrection,
} from "@/api/correction";
import { useAgentStream } from "@/hooks/useAgentStream";

beforeEach(() => vi.restoreAllMocks());

// ── sse.ts type guards 缺口 ────────────────────────────────────────────────
describe("type guards (additional)", () => {
  it("isToolResult narrows tool_result variant", () => {
    expect(
      isToolResult({
        event: "tool_result",
        data: { tool_call_id: "t", status: "ok", output: "result" },
      }),
    ).toBe(true);
    expect(
      isToolResult({ event: "text_delta", data: { delta: "x" } } as any),
    ).toBe(false);
  });
  it("isClarify narrows clarify_request variant", () => {
    expect(
      isClarify({
        event: "clarify_request",
        data: { pending_id: 1, question: "q" },
      }),
    ).toBe(true);
    expect(
      isClarify({ event: "agent_started", data: { trace_id: "t", started_at: "" } } as any),
    ).toBe(false);
  });
});

// ── correction.ts 缺口: cancelStream / fetchActiveWorkers / fetchStreamStatus
describe("api/correction — additional endpoints", () => {
  it("fetchStreamStatus GET /status, ok=true", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "running", trace_id: "X" }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const r = await fetchStreamStatus("X");
    expect(r.status).toBe("running");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/query/stream/X/status",
      expect.objectContaining({ credentials: "include" }),
    );
  });
  it("fetchStreamStatus 非 200 → throws", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("oops", { status: 500 })),
    );
    await expect(fetchStreamStatus("X")).rejects.toThrow(/500/);
  });
  it("fetchActiveWorkers ok 返 trace_ids", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ trace_ids: ["a", "b"] }), { status: 200 }),
      ),
    );
    const r = await fetchActiveWorkers();
    expect(r.trace_ids).toEqual(["a", "b"]);
  });
  it("fetchActiveWorkers 非 200 → throws", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("err", { status: 503 })),
    );
    await expect(fetchActiveWorkers()).rejects.toThrow(/503/);
  });
  it("submitCorrection 非 200 → throws", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("e", { status: 400 })),
    );
    await expect(
      submitCorrection("t", { action: "abort", instruction: "" }),
    ).rejects.toThrow(/400/);
  });
  it("cancelStream success", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ cancelled: true }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const r = await cancelStream("trX");
    expect(r.cancelled).toBe(true);
  });
});

// ── useAgentStream hook: start / stop / 错误路径 ───────────────────────────
function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
}

describe("useAgentStream hook", () => {
  it("start → 累积事件到 state, status: idle → running → finished", async () => {
    const stream = streamFromChunks([
      'event: agent_started\ndata: {"trace_id":"abc","started_at":"now"}\n\n',
      'event: text_delta\ndata: {"delta":"hi"}\n\n',
      'event: agent_finished\ndata: {"stop_reason":"end_turn","total_iterations":1}\n\n',
    ]);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(stream, {
          status: 200,
          headers: { "X-Trace-Id": "abc", "Content-Type": "text/event-stream" },
        }),
      ),
    );
    const { result } = renderHook(() => useAgentStream());
    await act(async () => {
      await result.current.start({ namespace_id: 1, question: "q" });
    });
    await waitFor(() => expect(result.current.state.status).toBe("finished"));
    expect(result.current.state.thinking).toBe("hi");
    expect(result.current.state.traceId).toBe("abc");
  });

  it("fetch 抛非 AbortError → state.errors 收到错误", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("net down")));
    const { result } = renderHook(() => useAgentStream());
    await act(async () => {
      await result.current.start({ namespace_id: 1, question: "q" });
    });
    await waitFor(() =>
      expect(result.current.state.errors.length).toBeGreaterThan(0),
    );
    expect(result.current.state.status).toBe("error");
  });

  it("stop() 调用 abortRef.abort, 不抛", () => {
    const { result } = renderHook(() => useAgentStream());
    act(() => result.current.stop());
    // 第二次 stop 也安全
    act(() => result.current.stop());
  });

  it("warning / error 事件累积进 state.warnings / state.errors", async () => {
    const stream = streamFromChunks([
      'event: warning\ndata: {"message":"mongo slow"}\n\n',
      'event: error\ndata: {"code":"internal","message":"oh no","recoverable":false}\n\n',
    ]);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(stream, {
          status: 200,
          headers: { "X-Trace-Id": "t1" },
        }),
      ),
    );
    const { result } = renderHook(() => useAgentStream());
    await act(async () => {
      await result.current.start({ namespace_id: 1, question: "q" });
    });
    await waitFor(() => expect(result.current.state.warnings).toContain("mongo slow"));
    expect(result.current.state.errors).toContain("oh no");
  });

  it("cancelled 事件 → status=cancelled", async () => {
    const stream = streamFromChunks([
      'event: cancelled\ndata: {}\n\n',
    ]);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(stream, { status: 200, headers: { "X-Trace-Id": "t" } }),
      ),
    );
    const { result } = renderHook(() => useAgentStream());
    await act(async () => {
      await result.current.start({ namespace_id: 1, question: "q" });
    });
    await waitFor(() => expect(result.current.state.status).toBe("cancelled"));
  });

  it("final_answer 事件落 state.finalAnswer", async () => {
    const stream = streamFromChunks([
      'event: final_answer\ndata: {"content":"done","history_id":7,"columns":["a"],"rows":[{"a":1}],"chart_type":"table"}\n\n',
    ]);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(stream, { status: 200, headers: { "X-Trace-Id": "t" } }),
      ),
    );
    const { result } = renderHook(() => useAgentStream());
    await act(async () => {
      await result.current.start({ namespace_id: 1, question: "q" });
    });
    await waitFor(() =>
      expect(result.current.state.finalAnswer?.content).toBe("done"),
    );
    expect(result.current.state.finalAnswer?.historyId).toBe(7);
  });
});

// ── sseClient 缺口: 非 200 / body=null / parseSSEChunk leftover wrap ─────
import { openAgentStream, parseSSEChunk } from "@/api/sseClient";

describe("sseClient additional", () => {
  it("openAgentStream 非 200 → throw", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("err", { status: 502 })),
    );
    await expect(
      openAgentStream({ url: "/x", body: {}, onEvent: () => {} }),
    ).rejects.toThrow(/502/);
  });

  it("parseSSEChunk 累积 leftover, 后一轮拼上完成事件", () => {
    const r1 = parseSSEChunk('event: text_delta\ndata: {"delta":"par', "");
    expect(r1.events).toHaveLength(0);
    const r2 = parseSSEChunk('tial"}\n\n', r1.leftover);
    expect(r2.events).toHaveLength(1);
    expect((r2.events[0] as any).data.delta).toBe("partial");
  });

  it("parseSSEChunk malformed JSON → 跳过不抛", () => {
    const { events } = parseSSEChunk("event: text_delta\ndata: {bad}\n\n", "");
    expect(events).toHaveLength(0);
  });

  it("parseSSEChunk no event: 行 → skip", () => {
    const { events } = parseSSEChunk("data: {}\n\n", "");
    expect(events).toHaveLength(0);
  });

  it("openAgentStream onError 在 reader 报错时被调用", async () => {
    // ReadableStream 抛错让 reader.read() reject
    const stream = new ReadableStream({
      start(c) {
        c.error(new Error("network died"));
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(stream, { status: 200, headers: { "X-Trace-Id": "t" } }),
      ),
    );
    const onErr = vi.fn();
    const handle = await openAgentStream({
      url: "/x",
      body: {},
      onEvent: () => {},
      onError: onErr,
    });
    await expect(handle.done).rejects.toThrow();
    expect(onErr).toHaveBeenCalled();
  });
});
