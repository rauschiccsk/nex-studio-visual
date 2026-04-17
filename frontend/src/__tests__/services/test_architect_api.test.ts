/**
 * Unit tests for the Architect API client.
 *
 * These tests mock the global ``fetch`` function to verify that each
 * API helper issues the correct HTTP method, URL and body without
 * hitting a real backend.  The streaming ``sendMessageStream`` tests
 * use a mock ``ReadableStream`` to simulate SSE / NDJSON events.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import {
  createSessionApi,
  listSessionsApi,
  getSessionApi,
  closeSessionApi,
  listMessagesApi,
  sendMessageStream,
} from "@/services/api/architect";
import type { ArchitectSessionRead } from "@/types/architectSession";
import type { ArchitectMessageRead } from "@/types/architectMessage";
import type { PaginatedResponse } from "@/types/common";
import type { ArchitectStreamEvent } from "@/types/architect";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

/** Minimal ``ArchitectSessionRead`` fixture. */
function makeSession(
  overrides: Partial<ArchitectSessionRead> = {},
): ArchitectSessionRead {
  return {
    id: "sess-aaaa-bbbb-cccc-dddddddddddd",
    project_id: "proj-1111-2222-3333-444444444444",
    module_id: null,
    status: "active",
    created_by: "user-0000-0000-0000-000000000000",
    closed_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

/** Minimal ``ArchitectMessageRead`` fixture. */
function makeMessage(
  overrides: Partial<ArchitectMessageRead> = {},
): ArchitectMessageRead {
  return {
    id: "msg-aaaa-bbbb-cccc-dddddddddddd",
    session_id: "sess-aaaa-bbbb-cccc-dddddddddddd",
    role: "user",
    content: "Hello",
    input_tokens: null,
    output_tokens: null,
    cost_usd: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

/** Create a ``Response``-like object that ``fetch`` would return. */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    headers: new Headers({ "content-type": "application/json" }),
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

/**
 * Build a ``Response`` that simulates an SSE stream.
 *
 * Each string in ``lines`` is yielded as a separate chunk from the
 * underlying ``ReadableStream``.
 */
function sseResponse(lines: string[]): Response {
  const encoder = new TextEncoder();
  let index = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index < lines.length) {
        controller.enqueue(encoder.encode(lines[index]));
        index++;
      } else {
        controller.close();
      }
    },
  });

  return {
    ok: true,
    status: 200,
    statusText: "OK",
    headers: new Headers({ "content-type": "text/event-stream" }),
    body: stream,
    text: () => Promise.resolve(lines.join("")),
    json: () => Promise.reject(new Error("Not JSON")),
  } as unknown as Response;
}

/* ------------------------------------------------------------------ */
/*  Setup                                                              */
/* ------------------------------------------------------------------ */

let fetchMock: Mock;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("localStorage", {
    getItem: vi.fn(() => "test-jwt-token"),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

/* ------------------------------------------------------------------ */
/*  Session CRUD tests                                                 */
/* ------------------------------------------------------------------ */

describe("createSessionApi", () => {
  it("sends POST /projects/{projectId}/architect with body", async () => {
    const session = makeSession();
    fetchMock.mockResolvedValueOnce(jsonResponse(session));

    const result = await createSessionApi("proj-1", {
      project_id: "proj-1",
      created_by: "user-1",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/projects/proj-1/architect");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      project_id: "proj-1",
      created_by: "user-1",
    });
    expect(result).toEqual(session);
  });
});

describe("listSessionsApi", () => {
  it("sends GET /projects/{projectId}/architect", async () => {
    const paginated: PaginatedResponse<ArchitectSessionRead> = {
      items: [makeSession()],
      total: 1,
      skip: 0,
      limit: 50,
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(paginated));

    const result = await listSessionsApi("proj-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/projects/proj-1/architect");
    expect(init.method).toBe("GET");
    expect(result).toEqual(paginated);
  });

  it("passes optional filter params", async () => {
    const paginated: PaginatedResponse<ArchitectSessionRead> = {
      items: [],
      total: 0,
      skip: 0,
      limit: 10,
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(paginated));

    await listSessionsApi("proj-1", {
      status: "active",
      limit: 10,
    });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("status=active");
    expect(url).toContain("limit=10");
  });
});

describe("getSessionApi", () => {
  it("sends GET /architect/sessions/{sessionId}", async () => {
    const session = makeSession();
    fetchMock.mockResolvedValueOnce(jsonResponse(session));

    const result = await getSessionApi("sess-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/architect/sessions/sess-1");
    expect(init.method).toBe("GET");
    expect(result).toEqual(session);
  });
});

describe("closeSessionApi", () => {
  it("sends POST /architect/sessions/{sessionId}/close", async () => {
    const closed = makeSession({ status: "closed", closed_at: "2026-01-02T00:00:00Z" });
    fetchMock.mockResolvedValueOnce(jsonResponse(closed));

    const result = await closeSessionApi("sess-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/architect/sessions/sess-1/close");
    expect(init.method).toBe("POST");
    expect(result).toEqual(closed);
  });
});

/* ------------------------------------------------------------------ */
/*  Messages tests                                                     */
/* ------------------------------------------------------------------ */

describe("listMessagesApi", () => {
  it("sends GET /architect/sessions/{sessionId}/messages", async () => {
    const paginated: PaginatedResponse<ArchitectMessageRead> = {
      items: [makeMessage()],
      total: 1,
      skip: 0,
      limit: 50,
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(paginated));

    const result = await listMessagesApi("sess-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/architect/sessions/sess-1/messages");
    expect(init.method).toBe("GET");
    expect(result).toEqual(paginated);
  });

  it("passes pagination params", async () => {
    const paginated: PaginatedResponse<ArchitectMessageRead> = {
      items: [],
      total: 0,
      skip: 10,
      limit: 20,
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(paginated));

    await listMessagesApi("sess-1", { skip: 10, limit: 20 });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("skip=10");
    expect(url).toContain("limit=20");
  });
});

/* ------------------------------------------------------------------ */
/*  Streaming tests                                                    */
/* ------------------------------------------------------------------ */

describe("sendMessageStream", () => {
  it("sends POST with correct URL, headers, and body", async () => {
    const sseLines = [
      'data: {"type":"chunk","content":"Hello"}\n\n',
      'data: {"type":"done","content":"Hello world","tokens":{"input_tokens":10,"output_tokens":5}}\n\n',
    ];
    fetchMock.mockResolvedValueOnce(sseResponse(sseLines));

    const onChunk = vi.fn();
    const onDone = vi.fn();

    sendMessageStream("sess-1", "Hi there", onChunk, onDone);

    // Wait for the async stream consumption to complete.
    await vi.waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/architect/sessions/sess-1/message");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Authorization: "Bearer test-jwt-token",
    });
    expect(JSON.parse(init.body as string)).toEqual({ content: "Hi there" });
  });

  it("calls onChunk for each chunk event", async () => {
    const sseLines = [
      'data: {"type":"chunk","content":"Hello "}\n\n',
      'data: {"type":"chunk","content":"world"}\n\n',
      'data: {"type":"done","content":"Hello world","tokens":{"input_tokens":null,"output_tokens":null}}\n\n',
    ];
    fetchMock.mockResolvedValueOnce(sseResponse(sseLines));

    const chunks: string[] = [];
    const onChunk = vi.fn((c: string) => chunks.push(c));
    const onDone = vi.fn();

    sendMessageStream("sess-1", "Test", onChunk, onDone);

    await vi.waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));

    expect(onChunk).toHaveBeenCalledTimes(2);
    expect(chunks).toEqual(["Hello ", "world"]);
  });

  it("calls onDone with the full event payload", async () => {
    const doneEvent: ArchitectStreamEvent & { type: "done" } = {
      type: "done",
      content: "Complete response",
      tokens: { input_tokens: 100, output_tokens: 50 },
    };
    const sseLines = [`data: ${JSON.stringify(doneEvent)}\n\n`];
    fetchMock.mockResolvedValueOnce(sseResponse(sseLines));

    const onChunk = vi.fn();
    const onDone = vi.fn();

    sendMessageStream("sess-1", "Test", onChunk, onDone);

    await vi.waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));

    expect(onDone).toHaveBeenCalledWith(doneEvent);
  });

  it("calls onError for error events from the stream", async () => {
    const sseLines = [
      'data: {"type":"error","content":"Claude timeout"}\n\n',
      'data: {"type":"done","content":"","tokens":{"input_tokens":null,"output_tokens":null}}\n\n',
    ];
    fetchMock.mockResolvedValueOnce(sseResponse(sseLines));

    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();

    sendMessageStream("sess-1", "Test", onChunk, onDone, onError);

    await vi.waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0]![0]).toBeInstanceOf(Error);
    expect((onError.mock.calls[0]![0] as Error).message).toBe("Claude timeout");
  });

  it("calls onError on HTTP error responses", async () => {
    const errorResp = {
      ok: false,
      status: 403,
      statusText: "Forbidden",
      headers: new Headers({ "content-type": "application/json" }),
      text: () => Promise.resolve('{"detail":"ri role required"}'),
      body: null,
    } as unknown as Response;
    fetchMock.mockResolvedValueOnce(errorResp);

    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();

    sendMessageStream("sess-1", "Test", onChunk, onDone, onError);

    await vi.waitFor(() => expect(onError).toHaveBeenCalledTimes(1));

    expect(onError.mock.calls[0]![0]).toBeInstanceOf(Error);
    expect((onError.mock.calls[0]![0] as Error).message).toContain("403");
  });

  it("returns an AbortController for cancellation", () => {
    fetchMock.mockResolvedValueOnce(
      sseResponse([
        'data: {"type":"done","content":"","tokens":{"input_tokens":null,"output_tokens":null}}\n\n',
      ]),
    );

    const controller = sendMessageStream(
      "sess-1",
      "Test",
      vi.fn(),
      vi.fn(),
    );

    expect(controller).toBeInstanceOf(AbortController);
    // Calling abort should not throw.
    controller.abort();
  });

  it("skips malformed JSON lines gracefully", async () => {
    const sseLines = [
      'data: {"type":"chunk","content":"ok"}\n\n',
      "data: NOT_VALID_JSON\n\n",
      'data: {"type":"done","content":"ok","tokens":{"input_tokens":null,"output_tokens":null}}\n\n',
    ];
    fetchMock.mockResolvedValueOnce(sseResponse(sseLines));

    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();

    sendMessageStream("sess-1", "Test", onChunk, onDone, onError);

    await vi.waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));

    // The malformed line should be silently skipped.
    expect(onChunk).toHaveBeenCalledTimes(1);
    expect(onChunk).toHaveBeenCalledWith("ok");
    expect(onError).not.toHaveBeenCalled();
  });
});
