import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BUILD_ID } from "@/pwa/buildId";
import { registerServiceWorker } from "@/pwa/registerServiceWorker";

/**
 * Enabling installability.
 *
 * WHAT THESE TESTS PROVE: that the app asks for registration correctly - at the right
 * URL, at the right moment, and without a failure crashing it.
 *
 * WHAT THEY DO NOT PROVE: that the service worker really registers and that the app can
 * be installed. That cannot be verified in a browser stand-in, and pretending it can
 * would be worse than admitting it - **a person verifies it** (manually, on the deployed
 * HTTPS address).
 */

const register = vi.fn<(url: string) => Promise<ServiceWorkerRegistration>>(() =>
  Promise.resolve({} as ServiceWorkerRegistration),
);

beforeEach(() => {
  // A listener from a previous test that was not consumed would fire only here - fire
  // `load` once to flush it, and only then clear the call record.
  window.dispatchEvent(new Event("load"));
  register.mockClear();
  register.mockResolvedValue({} as ServiceWorkerRegistration);
  Object.defineProperty(navigator, "serviceWorker", {
    value: { register },
    writable: true,
    configurable: true,
  });
});

afterEach(() => vi.restoreAllMocks());

/** Fires the `load` that the registration hangs on. */
function pageLoaded() {
  window.dispatchEvent(new Event("load"));
}

describe("enabling installability", () => {
  it("does NOT register at once - it waits until the app has loaded", () => {
    // Otherwise it would compete for bandwidth exactly when the person waits for the screen.
    registerServiceWorker();

    expect(register).not.toHaveBeenCalled();
  });

  it("after load it registers WITH THE BUILD FINGERPRINT in the URL", () => {
    // The fingerprint is there because the content does not change between versions.
    // Without it the browser might not notice a new registration and the offline notice
    // would stay stale in its cache forever.
    registerServiceWorker();
    pageLoaded();

    expect(register).toHaveBeenCalledTimes(1);
    const url = register.mock.calls[0]![0];
    expect(url).toContain("/sw.js");
    expect(url).toContain(BUILD_ID);
  });

  it("a failed registration does NOT crash the app", async () => {
    // One can live without installability; not without the app.
    register.mockRejectedValue(new Error("browser refused"));
    const unhandled: unknown[] = [];
    const record = (r: unknown) => unhandled.push(r);
    process.on("unhandledRejection", record);

    registerServiceWorker();
    expect(() => pageLoaded()).not.toThrow();
    await new Promise((r) => setTimeout(r, 30));

    process.off("unhandledRejection", record);
    expect(unhandled).toEqual([]);
  });

  it("in a browser without support it does not even try - and THROWS NOTHING", async () => {
    // `"serviceWorker" in navigator` is true even when the value is `undefined` - exactly
    // how Firefox behaves in a private window. A check made up front would pass and
    // reaching for `.register` would then fail on nothing.
    //
    // The error would arise only inside the `load` handler, i.e. outside the test body:
    // it would stay green outwardly while the run still ended in failure. So the errors
    // are collected here.
    const errors: unknown[] = [];
    const collect = (e: ErrorEvent) => {
      errors.push(e.error ?? e.message);
      e.preventDefault();
    };
    window.addEventListener("error", collect);

    Object.defineProperty(navigator, "serviceWorker", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    registerServiceWorker();
    pageLoaded();
    await new Promise((r) => setTimeout(r, 20));

    window.removeEventListener("error", collect);
    expect(errors, "the app threw an error when support was missing").toEqual([]);
    expect(register).not.toHaveBeenCalled();
  });
});
