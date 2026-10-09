import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BUILD_ID } from "@/pwa/buildId";
import { useVersionWatch } from "@/pwa/useVersionWatch";

/**
 * ===========================================================================
 *  HARD GATE
 * ===========================================================================
 *
 * The requirement says: "on every startup the app must first ask the server whether a
 * newer version exists and, if so, reload itself. This claim must have its own test that
 * CAN FAIL. Without it, installability is not switched on."
 *
 * This is that test - and it is done OVER THE HOOK, not over the pure function
 * `decide()`. That is its whole point: `decide()` verifies how the app decides ONCE IT
 * HAS an answer. If someone removed the server query from the app, all `decide()` tests
 * would stay GREEN and nobody would notice. This test fails then.
 *
 * Verified by falsification: after temporarily removing the `fetch` call from the hook,
 * all three statements below fail.
 */

const reload = vi.fn();

beforeEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
  reload.mockClear();
  Object.defineProperty(window, "location", {
    value: { reload, assign: vi.fn(), href: "/", pathname: "/" },
    writable: true,
    configurable: true,
  });
});

/** A server reporting the given build fingerprint. */
function server(buildId: string) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    json: async () => ({ buildId }),
  } as unknown as Response);
}

describe("HARD GATE: the app asks the server at startup", () => {
  it("1 - after mounting, /version.json is REALLY requested, forbidding a cached copy", async () => {
    const fetchMock = server(BUILD_ID);

    renderHook(() => useVersionWatch());

    // No interaction is awaited - the query must come by itself, right after mounting.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith("/version.json", { cache: "no-store" });
  });

  it("2 - a different fingerprint -> the app reloads", async () => {
    server("SOME-OTHER-BUILD");

    renderHook(() => useVersionWatch());

    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
  });

  it("3 - a MATCHING fingerprint -> the app does NOT reload (negative counterpart to point 2)", async () => {
    // Without this statement point 2 would also pass something that reloads ALWAYS. Only
    // the pair proves that the app not only asks but also understands the answer.
    const fetchMock = server(BUILD_ID);

    renderHook(() => useVersionWatch());

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    // Let everything that might still fire run its course.
    await new Promise((r) => setTimeout(r, 30));
    expect(reload).not.toHaveBeenCalled();
  });
});
