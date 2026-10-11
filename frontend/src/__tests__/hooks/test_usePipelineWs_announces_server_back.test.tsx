/**
 * DEV-63 — every cockpit deploy restarts the backend and drops the board's connection. When it comes back, the window
 * says so (`announceServerBack`), and the version watch asks at once whether a newer version is out. The first connect
 * of a window is not a comeback and says nothing.
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
import { SERVER_BACK_EVENT } from "@/pwa/serverBack";
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
  _drop() {
    this.readyState = 3;
    this.onclose?.();
  }
}

describe("usePipelineWs — the server coming back is announced (DEV-63)", () => {
  let announced = 0;
  const count = () => {
    announced += 1;
  };

  beforeEach(() => {
    FakeWS.instances = [];
    announced = 0;
    usePresenceStore.setState({ isAway: false });
    vi.stubGlobal("WebSocket", FakeWS as unknown as typeof WebSocket);
    vi.useFakeTimers();
    window.addEventListener(SERVER_BACK_EVENT, count);
  });
  afterEach(() => {
    window.removeEventListener(SERVER_BACK_EVENT, count);
    vi.useRealTimers();
  });

  it("the first connect of a window is not a comeback", () => {
    renderHook(() => usePipelineWs("v1"));
    act(() => FakeWS.instances[0]!._open());

    expect(announced).toBe(0);
  });

  it("a connection that dropped and came back announces it — once per comeback", () => {
    renderHook(() => usePipelineWs("v1"));
    act(() => FakeWS.instances[0]!._open());

    act(() => FakeWS.instances[0]!._drop());
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(announced).toBe(0); // still down — nothing to ask about yet
    act(() => FakeWS.instances[1]!._open());
    expect(announced).toBe(1);

    act(() => FakeWS.instances[1]!._drop());
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    act(() => FakeWS.instances[2]!._open());
    expect(announced).toBe(2);
  });

  it("a window opened during a deploy announces the server once it finally answers", () => {
    // The first attempt fails because the backend is restarting; the version asked at load may have been the old
    // one — when the server answers, the window must ask again.
    renderHook(() => usePipelineWs("v1"));
    act(() => FakeWS.instances[0]!._drop());
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    act(() => FakeWS.instances[1]!._open());

    expect(announced).toBe(1);
  });
});
