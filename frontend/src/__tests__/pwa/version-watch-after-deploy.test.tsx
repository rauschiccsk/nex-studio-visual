import { act, render, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { SETTINGS } from "@/config/settings";
import { BUILD_ID } from "@/pwa/buildId";
import { announceServerBack } from "@/pwa/serverBack";
import { useVersionWatch } from "@/pwa/useVersionWatch";

/**
 * DEV-63 — an open window offers the new version right after a deploy, not up to 15 minutes later.
 *
 * 11.10.2026 the cockpit 4.43.22 went live at 17:28; at 17:37 the window in which the Director ran the Career
 * Asistent build still showed v4.43.21 and offered nothing. The window had been on screen the whole time: it asked
 * only at load, on coming back from hidden, and every 15 minutes — switching between two open windows is none of
 * those. Now it also asks when the person switches to it and when its connection to the server comes back (every
 * deploy restarts the backend), and the periodic check runs every minute.
 */

const reload = vi.fn();

function setPath(path: string) {
  window.history.pushState({}, "", path);
  Object.defineProperty(window, "location", {
    value: {
      reload,
      assign: vi.fn(),
      replace: vi.fn(),
      origin: "http://localhost:3000",
      href: `http://localhost:3000${path}`,
      protocol: "http:",
      host: "localhost:3000",
      hostname: "localhost",
      port: "3000",
      pathname: path,
      search: "",
      hash: "",
    },
    writable: true,
    configurable: true,
  });
}

/** The server reports `buildId`; returns the spy, filtered later to the version questions. */
function server(buildId: string) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    json: async () => ({ buildId }),
  } as unknown as Response);
}

const versionQuestions = (f: ReturnType<typeof server>) => f.mock.calls.filter(([url]) => url === "/version.json");

beforeEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
  localStorage.clear();
  reload.mockClear();
  setPath("/");
});
afterEach(() => vi.useRealTimers());

/** A window that started on the current version, while the server meanwhile got a new one. */
async function windowOpenWhileANewVersionComesOut() {
  const f = server(BUILD_ID);
  const { result } = renderHook(() => useVersionWatch());
  await waitFor(() => expect(versionQuestions(f)).toHaveLength(1));
  f.mockResolvedValue({ ok: true, json: async () => ({ buildId: "NEW-VERSION" }) } as unknown as Response);
  return { f, result };
}

describe("an open window learns about the new version at once (DEV-63)", () => {
  it("switching to the window asks the server — and offers the bar, never reloads by itself", async () => {
    const { f, result } = await windowOpenWhileANewVersionComesOut();

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });

    await waitFor(() => expect(result.current.updateAvailable).toBe(true));
    expect(versionQuestions(f)).toHaveLength(2);
    expect(reload).not.toHaveBeenCalled();
  });

  it("the connection to the server coming back asks the server — every deploy restarts it", async () => {
    const { f, result } = await windowOpenWhileANewVersionComesOut();

    act(() => announceServerBack());

    await waitFor(() => expect(result.current.updateAvailable).toBe(true));
    expect(versionQuestions(f)).toHaveLength(2);
    expect(reload).not.toHaveBeenCalled();
  });

  it("after the window is gone, neither switching to it nor the server coming back asks anything", async () => {
    const f = server(BUILD_ID);
    const { unmount } = renderHook(() => useVersionWatch());
    await waitFor(() => expect(versionQuestions(f)).toHaveLength(1));
    unmount();

    window.dispatchEvent(new Event("focus"));
    announceServerBack();
    await new Promise((r) => setTimeout(r, 20));

    expect(versionQuestions(f)).toHaveLength(1);
  });
});

describe("the app asks every minute, not every 15 (DEV-63)", () => {
  it("the interval is a setting of the frontend and short enough to matter after a deploy", () => {
    expect(SETTINGS.VERSION_CHECK_INTERVAL_MS).toBeGreaterThan(0);
    expect(SETTINGS.VERSION_CHECK_INTERVAL_MS).toBeLessThanOrEqual(60_000);
  });

  it("the app asks again once that interval has passed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const f = server("dev"); // the fingerprint of a development build — the window is current
    setPath("/login");

    render(<App />);
    await waitFor(() => expect(versionQuestions(f)).toHaveLength(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SETTINGS.VERSION_CHECK_INTERVAL_MS);
    });

    expect(versionQuestions(f).length).toBeGreaterThanOrEqual(2);
  });
});
