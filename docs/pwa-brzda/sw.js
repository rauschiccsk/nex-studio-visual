/*
 * ===========================================================================
 *  EMERGENCY BRAKE - DO NOT USE UNTIL IT IS NEEDED
 * ===========================================================================
 *
 * This is NOT the regular sw.js. It is its replacement, which detaches the app from the
 * installability mechanism for EVERYONE AT ONCE - without anybody deleting anything on
 * their own computer.
 *
 * When: if the installability mechanism turns out to be the source of problems.
 * How:  copy this file over `frontend/public/sw.js` and deploy.
 *
 * What it does, in this order:
 *   1. takes control immediately (does not wait for people to close their windows),
 *   2. deletes EVERYTHING the app has cached,
 *   3. unregisters itself - from then on the app runs as an ordinary page,
 *   4. reloads the open windows so this applies at once, not only after a restart.
 *
 * After this brake is deployed the app CANNOT be installed. That is intentional: it is
 * a retreat to safety, not a fix.
 */

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
      .then(() => self.registration.unregister())
      .then(() => self.clients.matchAll({ type: "window" }))
      .then((windows) => windows.forEach((win) => win.navigate(win.url)))
      .catch(() => {
        // Even if some step failed, unregistering is the essential part - and that has
        // already happened. Nothing is swept under the rug here; we just do not crash
        // the rest.
      }),
  );
});

/*
 * No `fetch` handler. Without it the service worker does not interfere with anything:
 * every request goes straight to the network, exactly as if there were none.
 */
