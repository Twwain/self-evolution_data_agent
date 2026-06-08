import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { QueryStreamView } from "@/components/stream/QueryStreamView";
import { initialAgentStreamState } from "@/hooks/useAgentStream";

describe("QueryStreamView", () => {
  it("shows idle state initially", () => {
    render(
      <QueryStreamView
        state={initialAgentStreamState()}
        onStop={() => {}}
        onClarifyAnswer={() => {}}
        onCorrect={() => {}}
      />,
    );
    expect(screen.getByText(/idle/i)).toBeInTheDocument();
  });

  it("renders thinking + tools + final answer when present", () => {
    const s = {
      ...initialAgentStreamState(),
      status: "finished" as const,
      thinking: "step1",
      tools: [
        {
          toolCallId: "a",
          name: "lookup_knowledge",
          input: {},
          status: "ok" as const,
        },
      ],
      finalAnswer: {
        content: "answer text",
        historyId: 1,
        columns: ["x"],
        rows: [{ x: 1 }],
      },
    };
    render(
      <QueryStreamView
        state={s}
        onStop={() => {}}
        onClarifyAnswer={() => {}}
        onCorrect={() => {}}
      />,
    );
    expect(screen.getByText(/step1/)).toBeInTheDocument();
    expect(screen.getByText("lookup_knowledge")).toBeInTheDocument();
    expect(screen.getByText(/answer text/)).toBeInTheDocument();
  });
});
