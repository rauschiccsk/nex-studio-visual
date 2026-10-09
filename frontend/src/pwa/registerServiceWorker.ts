import { BUILD_ID } from "./buildId";

/**
 * Enables the mechanism that makes the browser treat the app as installable.
 *
 * Registration waits for `load` so it does not compete for bandwidth with loading the
 * app itself.
 *
 * The URL carries the build fingerprint on purpose: the content of `sw.js` does not
 * change between versions, so the browser might not notice a new registration and the
 * "no connection" notice would stay stale in its cache forever.
 */
export function registerServiceWorker(): void {
  if (typeof window === "undefined") return;

  // `once`: the listener removes itself after serving its purpose.
  window.addEventListener(
    "load",
    () => {
      // Support is checked NOW, at the moment of use, and by VALUE, not by key presence.
      // `"serviceWorker" in navigator` is true even when the value is `undefined`; that
      // is exactly how Firefox behaves in a private window. A check made up front would
      // pass and `.register` would then fail on nothing.
      const sw = typeof navigator !== "undefined" ? navigator.serviceWorker : undefined;
      if (!sw) return;

      void sw.register(`/sw.js?v=${BUILD_ID}`).catch(() => {
        // A failed registration only means the app will not be installable.
        // It must never crash or block the app itself, which works without it.
      });
    },
    { once: true },
  );
}
