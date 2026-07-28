/**
 * usePipelineWs — a PERMANENT refusal is not a transient drop.
 *
 * Every pipeline read is owner-or-ri (GET /pipeline/{id}, its /messages, and the WS, which closes with code
 * 4003), but the project LIST shows a Medior every project — so he can pin a project that is not his and open
 * Riadiace centrum. Before this fix the socket treated 4003 like a broken pipe: it reconnected forever against
 * a door that never opens, `scheduleReconnect` wiped the error on every drop, and the board stayed calmly
 * empty — a screen that lies about a permission denial.
 *
 * Guarded here: 4003 (and a REST 403) latch `accessDenied` + the plain-Slovak error, stop the reconnect loop
 * AND the 25s reconcile poll, the error survives the scheduler, an ordinary drop still reconnects (the fix
 * must not over-block), and a refusal never leaks onto the next version.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

const { getPipelineBoardApi } = vi.hoisted(() => ({
  getPipelineBoardApi: vi.fn(() => Promise.resolve({ state: null, recent_messages: [] })),
}));

vi.mock("@/store/authStore", () => ({
  useAuthStore: vi.fn((sel: (s: unknown) => unknown) => sel({ token: "jwt.token", user: null })),
}));
vi.mock("@/services/api/pipeline", () => ({
  getPipelineBoardApi,
  buildPipelineWsUrl: vi.fn(() => "ws://test/ws"),
}));

import { usePipelineWs, PIPELINE_ACCESS_DENIED_MESSAGE } from "@/hooks/usePipelineWs";
import { usePresenceStore } from "@/store/usePresenceStore";
import { ApiError } from "@/services/api";

// The backend's "forbidden" close code (backend/api/routes/pipeline.py).
const WS_FORBIDDEN = 4003;

class FakeWS {
  static instances: FakeWS[] = [];
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev?: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  send(d: string) {
    this.sent.push(d);
  }
  close() {
    this.readyState = 3;
  }
  _open() {
    this.readyState = FakeWS.OPEN;
    this.onopen?.();
  }
  /** Drop the socket; `code` mirrors the CloseEvent the browser delivers (omitted = no code). */
  _drop(code?: number) {
    this.readyState = 3;
    this.onclose?.(code === undefined ? undefined : { code });
  }
}

describe("usePipelineWs — permanent refusal (WS 4003 / REST 403)", () => {
  beforeEach(() => {
    FakeWS.instances = [];
    getPipelineBoardApi.mockClear();
    getPipelineBoardApi.mockImplementation(() => Promise.resolve({ state: null, recent_messages: [] }));
    usePresenceStore.setState({ isAway: false });
    vi.stubGlobal("WebSocket", FakeWS as unknown as typeof WebSocket);
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("stops reconnecting for good on close 4003 and names the refusal", () => {
    const { result } = renderHook(() => usePipelineWs("v1"));
    act(() => FakeWS.instances[0]!._open());
    expect(result.current.accessDenied).toBe(false);

    act(() => FakeWS.instances[0]!._drop(WS_FORBIDDEN));

    expect(result.current.accessDenied).toBe(true);
    expect(result.current.error).toBe(PIPELINE_ACCESS_DENIED_MESSAGE);
    // NOT the amber "spojenie stratené — obnovujem…" treatment: nothing is being restored here.
    expect(result.current.reconnecting).toBe(false);
    expect(result.current.connected).toBe(false);

    // No retry, ever — not after the first backoff, not after a minute of them.
    act(() => vi.advanceTimersByTime(60_000));
    expect(FakeWS.instances.length).toBe(1);
  });

  it("keeps the refusal message — the reconnect scheduler cannot overwrite it", () => {
    const { result } = renderHook(() => usePipelineWs("v1"));
    act(() => FakeWS.instances[0]!._open());
    act(() => FakeWS.instances[0]!._drop(WS_FORBIDDEN));
    expect(result.current.error).toBe(PIPELINE_ACCESS_DENIED_MESSAGE);

    // scheduleReconnect() clears `error` on every drop; a late/duplicate event must not blank the verdict.
    act(() => FakeWS.instances[0]!._drop());
    act(() => vi.advanceTimersByTime(60_000));
    expect(result.current.error).toBe(PIPELINE_ACCESS_DENIED_MESSAGE);
    expect(result.current.accessDenied).toBe(true);
    expect(FakeWS.instances.length).toBe(1);
  });

  it("stops the 25s reconcile poll too (no forever-403 against the same door)", () => {
    renderHook(() => usePipelineWs("v1"));
    act(() => FakeWS.instances[0]!._open());
    act(() => FakeWS.instances[0]!._drop(WS_FORBIDDEN));
    const callsAtRefusal = getPipelineBoardApi.mock.calls.length;

    act(() => vi.advanceTimersByTime(100_000)); // four reconcile ticks
    expect(getPipelineBoardApi.mock.calls.length).toBe(callsAtRefusal);
  });

  it("a REST 403 latches the same refusal and kills the socket", async () => {
    getPipelineBoardApi.mockRejectedValueOnce(new ApiError(403, "Nemáš prístup k tomuto projektu."));
    const { result } = renderHook(() => usePipelineWs("v1"));
    await act(async () => {}); // flush the rejected snapshot

    expect(result.current.accessDenied).toBe(true);
    expect(result.current.error).toBe(PIPELINE_ACCESS_DENIED_MESSAGE);
    expect(FakeWS.instances[0]!.readyState).toBe(3); // closed, not left hanging
    expect(FakeWS.instances[0]!.onclose).toBeNull(); // detached — it cannot arm one last retry

    const callsAtRefusal = getPipelineBoardApi.mock.calls.length;
    act(() => vi.advanceTimersByTime(100_000));
    expect(FakeWS.instances.length).toBe(1);
    expect(getPipelineBoardApi.mock.calls.length).toBe(callsAtRefusal);
  });

  it("an ordinary drop (no forbidden code) still reconnects — the fix must not over-block", () => {
    const { result } = renderHook(() => usePipelineWs("v1"));
    act(() => FakeWS.instances[0]!._open());

    act(() => FakeWS.instances[0]!._drop(1006)); // abnormal closure = a blip
    expect(result.current.accessDenied).toBe(false);
    expect(result.current.reconnecting).toBe(true);

    act(() => vi.advanceTimersByTime(1000));
    expect(FakeWS.instances.length).toBe(2);
  });

  it("a plain failed snapshot is NOT a refusal (only a 403 is)", async () => {
    getPipelineBoardApi.mockRejectedValueOnce(new ApiError(500, "boom"));
    const { result, unmount } = renderHook(() => usePipelineWs("v1"));
    await act(async () => {});

    expect(result.current.accessDenied).toBe(false);
    expect(result.current.error).not.toBe(PIPELINE_ACCESS_DENIED_MESSAGE);
    unmount();
  });

  it("does not leak the refusal onto the next version", () => {
    const { result, rerender } = renderHook(({ v }: { v: string }) => usePipelineWs(v), {
      initialProps: { v: "v1" },
    });
    act(() => FakeWS.instances[0]!._open());
    act(() => FakeWS.instances[0]!._drop(WS_FORBIDDEN));
    expect(result.current.accessDenied).toBe(true);

    rerender({ v: "v2" }); // pin a project this user DOES own
    expect(result.current.accessDenied).toBe(false);
    expect(result.current.error).toBeNull();
    expect(FakeWS.instances.length).toBe(2); // a fresh socket is allowed again
  });
});
