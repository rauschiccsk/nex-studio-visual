/**
 * usePipelineWs auto-reconnect (CR 2026-06-12) — a dropped socket reconnects with capped backoff and
 * re-fetches a fresh board snapshot; `reconnecting` surfaces the gap (false during the initial connect,
 * so it never flashes on load). Regression for the live incident where a backend redeploy killed the
 * WS, the board froze with no reconnect, and the Director's action buttons vanished until a hard refresh.
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

import { usePipelineWs } from "@/hooks/usePipelineWs";
import { usePresenceStore } from "@/store/usePresenceStore";

class FakeWS {
  static instances: FakeWS[] = [];
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
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
  _drop() {
    this.readyState = 3;
    this.onclose?.();
  }
}

describe("usePipelineWs — auto-reconnect (CR 2026-06-12)", () => {
  beforeEach(() => {
    FakeWS.instances = [];
    getPipelineBoardApi.mockClear();
    usePresenceStore.setState({ isAway: false });
    vi.stubGlobal("WebSocket", FakeWS as unknown as typeof WebSocket);
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("stays NOT-reconnecting during the initial connect (no flash on load)", () => {
    const { result } = renderHook(() => usePipelineWs("v1"));
    expect(FakeWS.instances.length).toBe(1); // socket created synchronously in the effect
    expect(result.current.reconnecting).toBe(false); // never connected yet → no stale banner
  });

  it("reconnects after a drop with backoff and re-fetches a fresh snapshot", () => {
    const { result } = renderHook(() => usePipelineWs("v1"));
    act(() => FakeWS.instances[0]!._open());
    expect(result.current.reconnecting).toBe(false);
    const snapshotsAfterConnect = getPipelineBoardApi.mock.calls.length;

    // socket drops → reconnecting surfaces, and no new socket until the backoff elapses
    act(() => FakeWS.instances[0]!._drop());
    expect(result.current.reconnecting).toBe(true);
    expect(FakeWS.instances.length).toBe(1);

    // first backoff = 1s → a new socket AND a fresh snapshot re-fetch (resync the frozen board)
    act(() => vi.advanceTimersByTime(1000));
    expect(FakeWS.instances.length).toBe(2);
    expect(getPipelineBoardApi.mock.calls.length).toBe(snapshotsAfterConnect + 1);

    // the new socket opens → reconnecting clears
    act(() => FakeWS.instances[1]!._open());
    expect(result.current.reconnecting).toBe(false);
  });

  it("reconciles the board from a fresh snapshot every 25s (safety net over the WS)", () => {
    renderHook(() => usePipelineWs("v1"));
    const afterConnect = getPipelineBoardApi.mock.calls.length; // initial connect fetched once
    act(() => vi.advanceTimersByTime(25_000));
    expect(getPipelineBoardApi.mock.calls.length).toBe(afterConnect + 1);
    act(() => vi.advanceTimersByTime(25_000));
    expect(getPipelineBoardApi.mock.calls.length).toBe(afterConnect + 2);
  });

  it("clears a stale board error when the socket drops (the reconnecting banner takes over)", async () => {
    vi.useRealTimers(); // this case needs no backoff advance; real microtask flush for the rejected snapshot
    getPipelineBoardApi.mockRejectedValueOnce(new Error("boom"));
    const { result, unmount } = renderHook(() => usePipelineWs("v1"));
    await act(async () => {}); // flush the rejected initial snapshot → error set
    expect(result.current.error).toBe("Načítanie prehľadu zlyhalo — skús to prosím znova.");

    act(() => FakeWS.instances[0]!._drop());
    expect(result.current.error).toBeNull(); // suppressed so it can't stack with the amber banner
    unmount(); // cancel the pending reconnect timer
  });

  it("does not reconnect after unmount (cleanup cancels the pending retry)", () => {
    const { result, unmount } = renderHook(() => usePipelineWs("v1"));
    act(() => FakeWS.instances[0]!._open());
    act(() => FakeWS.instances[0]!._drop());
    expect(result.current.reconnecting).toBe(true);

    unmount();
    act(() => vi.advanceTimersByTime(60000));
    expect(FakeWS.instances.length).toBe(1); // no socket created after unmount
  });
});
