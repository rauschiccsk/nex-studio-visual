import { renderHook, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useVersionWatch } from "@/pwa/useVersionWatch";
import { BUILD_ID } from "@/pwa/buildId";
import { COUNT_KEY, GUARD_KEY } from "@/pwa/versionCheck";

const reload = vi.fn();

beforeEach(() => {
  sessionStorage.clear();
  reload.mockClear();
  Object.defineProperty(window, "location", {
    value: { reload, assign: vi.fn(), href: "/" },
    writable: true,
    configurable: true,
  });
});
afterEach(() => vi.restoreAllMocks());

function server(buildId: string | null, ok = true) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok,
    json: async () => (buildId === null ? {} : { buildId }),
  } as unknown as Response);
}

describe("the version watch hook", () => {
  it("matching fingerprint -> the guard is reset (the 'does not reload' part is guarded by the startup gate)", async () => {
    sessionStorage.setItem(GUARD_KEY, "old");
    sessionStorage.setItem(COUNT_KEY, "1");
    const f = server(BUILD_ID);
    const { result } = renderHook(() => useVersionWatch());
    await waitFor(() => expect(f).toHaveBeenCalled());
    expect(reload).not.toHaveBeenCalled();
    expect(result.current.updateAvailable).toBe(false);
    await waitFor(() => expect(sessionStorage.getItem(GUARD_KEY)).toBeNull());
    expect(sessionStorage.getItem(COUNT_KEY)).toBeNull();
  });

  it("unreachable server -> no reload, no banner, the app keeps running", async () => {
    // A network error must be handled INSIDE the hook - it must not escape as unhandled,
    // otherwise the browser would report it as an app error.
    const unhandled: unknown[] = [];
    const record = (r: unknown) => unhandled.push(r);
    process.on("unhandledRejection", record);

    const f = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("no network"));
    const { result } = renderHook(() => useVersionWatch());
    await waitFor(() => expect(f).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 30));

    process.off("unhandledRejection", record);
    expect(unhandled).toEqual([]);
    expect(reload).not.toHaveBeenCalled();
    expect(result.current.updateAvailable).toBe(false);
  });

  it("server answers with an error -> the same, startup is not blocked", async () => {
    const f = server("OTHER", false);
    const { result } = renderHook(() => useVersionWatch());
    await waitFor(() => expect(f).toHaveBeenCalled());
    expect(reload).not.toHaveBeenCalled();
    expect(result.current.updateAvailable).toBe(false);
  });

  it("automatic reload switched off -> does NOT reload, only offers the banner", async () => {
    server("OTHER");
    const { result } = renderHook(() => useVersionWatch({ autoReload: false }));
    await waitFor(() => expect(result.current.updateAvailable).toBe(true));
    expect(reload).not.toHaveBeenCalled();
  });

  it("ceiling used up -> does NOT reload even with automatic reload switched on", async () => {
    sessionStorage.setItem(COUNT_KEY, "2");
    server("OTHER");
    const { result } = renderHook(() => useVersionWatch());
    await waitFor(() => expect(result.current.updateAvailable).toBe(true));
    expect(reload).not.toHaveBeenCalled();
  });

  it("window returns to the foreground -> asks again, but does NOT reload by itself", async () => {
    const f = server("OTHER");
    const { result } = renderHook(() => useVersionWatch({ autoReload: false }));
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(f).toHaveBeenCalledTimes(2));
    // The state is set after the response is parsed, so wait for it instead of racing it.
    await waitFor(() => expect(result.current.updateAvailable).toBe(true));
    expect(reload).not.toHaveBeenCalled();
  });

  it("the periodic check runs, and also does not reload by itself", async () => {
    vi.useFakeTimers();
    const f = server("OTHER");
    renderHook(() => useVersionWatch({ intervalMs: 1000, autoReload: false }));
    await vi.advanceTimersByTimeAsync(3500);
    expect(f.mock.calls.length).toBeGreaterThanOrEqual(4); // startup + 3 ticks
    expect(reload).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("a new version comes out DURING WORK -> a banner, not a silent reload", async () => {
    // A realistic course: the app starts as current, the person works in it and meanwhile
    // a new version comes out. Automatic reload is ON - the app must still not reload
    // itself, because it would discard a half-filled form.
    const f = server(BUILD_ID);
    const { result } = renderHook(() => useVersionWatch());
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1));

    f.mockResolvedValue({
      ok: true,
      json: async () => ({ buildId: "NEW-VERSION" }),
    } as unknown as Response);
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await waitFor(() => expect(result.current.updateAvailable).toBe(true));
    expect(reload).not.toHaveBeenCalled();
  });

  it("dismissing hides the banner", async () => {
    server("OTHER");
    const { result } = renderHook(() => useVersionWatch({ autoReload: false }));
    await waitFor(() => expect(result.current.updateAvailable).toBe(true));
    act(() => result.current.dismiss());
    expect(result.current.updateAvailable).toBe(false);
  });

  it("applyUpdate reloads the page", async () => {
    server(BUILD_ID);
    const { result } = renderHook(() => useVersionWatch());
    act(() => result.current.applyUpdate());
    expect(reload).toHaveBeenCalled();
  });

  it("after unmount nothing is reloaded any more", async () => {
    let release: (v: Response) => void = () => {};
    vi.spyOn(globalThis, "fetch").mockReturnValue(
      new Promise<Response>((r) => {
        release = r;
      }),
    );
    const { unmount } = renderHook(() => useVersionWatch());
    unmount();
    act(() =>
      release({ ok: true, json: async () => ({ buildId: "OTHER" }) } as unknown as Response),
    );
    await new Promise((r) => setTimeout(r, 20));
    expect(reload).not.toHaveBeenCalled();
  });
});
