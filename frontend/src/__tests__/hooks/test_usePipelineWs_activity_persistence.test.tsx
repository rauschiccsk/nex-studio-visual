/**
 * usePipelineWs — live-activity survives a route change (2026-06-30 fix). The agent_activity stream is
 * ephemeral (the WS never replays it on connect), so navigating away from the build page (it unmounts) and
 * back used to lose every streamed line and flash "Agent štartuje…". A per-version module cache now restores
 * the buffer on remount; a state change (run ended) clears it so a settled run never restores stale activity.
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
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  send() {}
  close() {
    this.readyState = 3;
  }
  _open() {
    this.readyState = FakeWS.OPEN;
    this.onopen?.();
  }
  _frame(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

const activityFrame = (line: string) => ({
  type: "agent_activity",
  stage: "programovanie",
  actor: "ai_agent",
  kind: "status",
  line,
});

describe("usePipelineWs — live-activity persists across a route change (2026-06-30)", () => {
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

  it("restores the streamed activity on remount (Vývoj → Metriky → Vývoj)", () => {
    const first = renderHook(() => usePipelineWs("ver-restore"));
    act(() => FakeWS.instances[0]!._open());
    act(() => FakeWS.instances[0]!._frame(activityFrame("číta súbory…")));
    expect(first.result.current.activity.map((a) => a.line)).toContain("číta súbory…");

    first.unmount(); // navigate away → the build page unmounts, hook state destroyed

    // navigate back → remount (same version) → activity RESTORED from the cache, not empty
    const second = renderHook(() => usePipelineWs("ver-restore"));
    expect(second.result.current.activity.map((a) => a.line)).toContain("číta súbory…");
    second.unmount();
  });

  it("a state change clears the cache so a settled run does not restore stale activity", () => {
    const first = renderHook(() => usePipelineWs("ver-settle"));
    act(() => FakeWS.instances[0]!._open());
    act(() => FakeWS.instances[0]!._frame(activityFrame("pracuje…")));
    expect(first.result.current.activity).toHaveLength(1);

    // a state_changed (board) ends the run → activity reset to [] (and the cache cleared in lock-step)
    act(() => FakeWS.instances[0]!._frame({ type: "state_changed", board: { state: null, recent_messages: [] } }));
    expect(first.result.current.activity).toEqual([]);

    first.unmount();
    const second = renderHook(() => usePipelineWs("ver-settle"));
    expect(second.result.current.activity).toEqual([]); // cache cleared → no stale restore
    second.unmount();
  });

  it("does not bleed one version's activity into another", () => {
    const a = renderHook(() => usePipelineWs("ver-A"));
    act(() => FakeWS.instances[0]!._open());
    act(() => FakeWS.instances[0]!._frame(activityFrame("A-line")));
    a.unmount();

    const b = renderHook(() => usePipelineWs("ver-B"));
    expect(b.result.current.activity).toEqual([]); // ver-B has its own (empty) buffer
    b.unmount();
  });
});
