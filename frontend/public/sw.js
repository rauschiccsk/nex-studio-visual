/*
 * NEX Studio Visual service worker.
 *
 * ===========================================================================
 *  THIS FILE DOES NOT CACHE THE APPLICATION. AT ALL.
 * ===========================================================================
 *
 * A service worker is required for the browser to consider the app installable - and
 * it is also the very mechanism that can serve an old version for days, without the
 * user being able to get rid of it. That is exactly what the specification warns about.
 *
 * Therefore there is NO "cache first" branch here and there must NEVER be one.
 * Navigation always goes to the network; the cache is touched only when the network
 * fails, and what comes back is a notice that contains not a bit of the application.
 * That an old version cannot appear is thus not a promise but a property of the
 * construction: it has nowhere to come from.
 *
 * What this file deliberately does NOT cache:
 *   - the entry page (/index.html),
 *   - the application bundle (/assets/*),
 *   - API responses.
 */

/*
 * Build fingerprint taken from the URL this file was registered with (/sw.js?v=...).
 * The content of sw.js does not change between versions, so without it the browser might
 * not notice a new registration and the offline notice would stay stale in its cache
 * forever. The same fingerprint also names the cache, so `activate` deletes the old one
 * by itself.
 */
const V = new URL(self.location).searchParams.get("v") || "dev";
const CACHE = `nex-studio-visual-shell-${V}`;

/* The only things that are cached: the offline notice and the mark it displays. */
const PRECACHE = ["/offline.html", "/icon.svg", "/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches
      .open(CACHE)
      .then((c) => c.addAll(PRECACHE))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  /*
   * Intervenes ONLY in navigations. Everything else - bundle, API, images - passes
   * untouched and follows the headers from the web server. The service worker thus
   * stands in the way of nothing it could serve stale.
   */
  if (e.request.mode !== "navigate") return;

  e.respondWith(
    fetch(e.request).catch(() =>
      /* The network failed - only NOW do we reach into the cache. */
      caches.match("/offline.html").then(
        (cached) =>
          cached ||
          /*
           * The stored notice was not found (e.g. it was not saved at install). A
           * comprehensible sentence beats an empty response the person could not
           * make sense of.
           */
          new Response(
            "<!doctype html><html lang=sk><meta charset=utf-8>" +
              "<title>NEX Studio Visual</title>" +
              '<body style="background:#0e1116;color:#e6e9ee;font-family:system-ui;' +
              'display:grid;place-items:center;height:100vh;margin:0;text-align:center">' +
              "<div><h1>Nie ste pripojení</h1>" +
              "<p>NEX Studio Visual potrebuje pripojenie k serveru.</p></div>",
            { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } },
          ),
      ),
    ),
  );
});
