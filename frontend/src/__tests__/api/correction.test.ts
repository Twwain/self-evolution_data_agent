import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  submitCorrection,
  submitClarifyResponse,
  cancelStream,
} from "@/api/correction";

beforeEach(() => vi.restoreAllMocks());

describe("correction API client", () => {
  it("submitCorrection POSTs to /correct with action+instruction", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await submitCorrection("trace1", { action: "abort", instruction: "stop now" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/query/stream/trace1/correct",
      expect.objectContaining({ method: "POST" }),
    );
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ correction_type: "abort", instruction: "stop now" });
  });

  it("cancelStream POSTs to /cancel", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await cancelStream("traceX");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/query/stream/traceX/cancel",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("submitClarifyResponse posts pending_id + answer", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await submitClarifyResponse("t1", { pending_id: 9, answer: "yes" });
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).toEqual({ pending_id: 9, answer: "yes" });
  });
});
