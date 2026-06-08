import { describe, it, expect } from "vitest";
import { agentStreamReducer, initialAgentStreamState } from "@/hooks/useAgentStream";
import type { AgentSSEEvent } from "@/types/sse";

const emit = (state: ReturnType<typeof initialAgentStreamState>, ev: AgentSSEEvent) =>
  agentStreamReducer(state, { type: "event", event: ev });

describe("agentStreamReducer", () => {
  it("text_delta accumulates into thinking text", () => {
    let s = initialAgentStreamState();
    s = emit(s, { event: "text_delta", data: { delta: "Hel" } });
    s = emit(s, { event: "text_delta", data: { delta: "lo" } });
    expect(s.thinking).toBe("Hello");
  });
  it("tool_use creates pending node, tool_result resolves it", () => {
    let s = initialAgentStreamState();
    s = emit(s, { event: "tool_use", data: { tool_call_id: "t1", name: "lookup_knowledge", input: { q: "x" } } });
    expect(s.tools).toHaveLength(1);
    expect(s.tools[0].status).toBe("pending");
    s = emit(s, { event: "tool_result", data: { tool_call_id: "t1", status: "ok", output: '{"hits":[]}' } });
    expect(s.tools[0].status).toBe("ok");
    expect(s.tools[0].output).toEqual('{"hits":[]}');
  });
  it("clarify_request sets pendingClarify, clarify_resolved clears", () => {
    let s = initialAgentStreamState();
    s = emit(s, { event: "clarify_request", data: { pending_id: 5, question: "q?" } });
    expect(s.pendingClarify?.pendingId).toBe(5);
    s = emit(s, { event: "clarify_resolved", data: { pending_id: 5, answer: "yes" } });
    expect(s.pendingClarify).toBeNull();
  });
  it("agent_finished sets status=finished", () => {
    let s = initialAgentStreamState();
    s = emit(s, { event: "agent_started", data: { trace_id: "a", started_at: "" } });
    expect(s.status).toBe("running");
    s = emit(s, { event: "agent_finished", data: { stop_reason: "end_turn", total_iterations: 2 } });
    expect(s.status).toBe("finished");
    expect(s.stopReason).toBe("end_turn");
  });
  it("cost_warning + knowledge_proposed accumulate", () => {
    let s = initialAgentStreamState();
    s = emit(s, { event: "cost_warning", data: { estimated_docs: 9_999_999, threshold: 5_000_000 } });
    s = emit(s, { event: "knowledge_proposed", data: { entry_id: 7, entry_type: "route_hint", preview: "..." } });
    expect(s.costWarnings).toHaveLength(1);
    expect(s.knowledgeProposed).toHaveLength(1);
  });
});
