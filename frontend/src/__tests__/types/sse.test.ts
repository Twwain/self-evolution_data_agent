import { describe, it, expect } from "vitest";
import type { AgentSSEEvent } from "@/types/sse";
import { isToolUse, isFinalAnswer } from "@/types/sse";

describe("AgentSSEEvent discriminated union", () => {
  it("narrows tool_use via type guard", () => {
    const ev: AgentSSEEvent = {
      event: "tool_use",
      data: { tool_call_id: "t1", name: "lookup_knowledge", input: { query: "x" } },
    };
    expect(isToolUse(ev)).toBe(true);
    if (isToolUse(ev)) expect(ev.data.name).toBe("lookup_knowledge");
  });
  it("narrows final_answer", () => {
    const ev: AgentSSEEvent = { event: "final_answer", data: { content: "done", history_id: 42 } };
    expect(isFinalAnswer(ev)).toBe(true);
  });
});
