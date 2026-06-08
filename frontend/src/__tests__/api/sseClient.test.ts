import { describe, it, expect, vi, beforeEach } from "vitest";
import { openAgentStream, parseSSEChunk } from "@/api/sseClient";
import type { AgentSSEEvent } from "@/types/sse";

function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
}

describe("parseSSEChunk", () => {
  it("parses single complete event", () => {
    const { events, leftover } = parseSSEChunk('event: text_delta\ndata: {"delta":"hi"}\n\n', "");
    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({ event: "text_delta", data: { delta: "hi" } });
    expect(leftover).toBe("");
  });
  it("preserves leftover incomplete event", () => {
    const { events, leftover } = parseSSEChunk('event: text_delta\ndata: {"del', "");
    expect(events).toHaveLength(0);
    expect(leftover).toContain("del");
  });
  it("ignores keepalive comments", () => {
    const { events } = parseSSEChunk(": keepalive\n\n", "");
    expect(events).toHaveLength(0);
  });
});

describe("openAgentStream", () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  it("emits events to onEvent callback", async () => {
    const stream = streamFromChunks([
      'event: agent_started\ndata: {"trace_id":"abc","started_at":"2026-05-01"}\n\n',
      'event: text_delta\ndata: {"delta":"hello"}\n\n',
      'event: agent_finished\ndata: {"stop_reason":"end_turn","total_iterations":1}\n\n',
    ]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(stream, {
      status: 200,
      headers: { "X-Trace-Id": "abc", "Content-Type": "text/event-stream" },
    })));
    const seen: AgentSSEEvent[] = [];
    const { traceId, done } = await openAgentStream({
      url: "/api/query/stream",
      body: { namespace_id: 1, question: "q", session_id: "s" },
      onEvent: (e) => seen.push(e),
    });
    await done;
    expect(traceId).toBe("abc");
    expect(seen.map((e) => e.event)).toEqual(["agent_started", "text_delta", "agent_finished"]);
  });
});
