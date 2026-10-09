import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * DEV-21 — the cockpit's web server never lets a browser keep the files that tell it about a new version.
 *
 * The update banner asks `/version.json`; the browser keeps `/sw.js` and the icons. Before DEV-21 the cockpit's
 * nginx gave every `.js`/`.png`/`.svg` a year of `immutable` cache — a cached `/version.json` would make the
 * new-version check blind, a cached service worker could serve an old cockpit for days, and a changed icon would not
 * reach anyone for a year. The rules live in `frontend/nginx.conf`, baked into the frontend image.
 */

const NGINX = readFileSync(resolve(__dirname, "../../../nginx.conf"), "utf-8");

function block(location: string): string {
  const start = NGINX.indexOf(`location ${location} {`);
  expect(start, `nginx.conf has no \`location ${location}\``).toBeGreaterThanOrEqual(0);
  return NGINX.slice(start, NGINX.indexOf("}", start));
}

describe("the files about the version are never kept by the browser", () => {
  it.each(["= /version.json", "= /sw.js"])("%s is sent with no-store", (location) => {
    expect(block(location)).toContain('Cache-Control "no-cache, no-store, must-revalidate"');
  });

  it("the manifest has its own content type — nginx does not know .webmanifest", () => {
    const manifest = block("= /manifest.webmanifest");
    expect(manifest).toContain("default_type application/manifest+json;");
    expect(manifest).toContain('Cache-Control "no-cache"');
  });

  it("the icons are revalidated, and their rule comes BEFORE the yearly cache rule", () => {
    // nginx matches regex locations in the order written — the yearly rule first would keep a changed icon a year.
    const icons = NGINX.indexOf("location ~* ^/(icon[^/]*\\.(svg|png)|favicon\\.ico)$ {");
    const yearly = NGINX.indexOf("location ~* \\.(?:js|css|png|jpg|jpeg|gif|ico|svg|webp|woff|woff2|ttf|eot)$ {");
    expect(icons).toBeGreaterThanOrEqual(0);
    expect(yearly).toBeGreaterThanOrEqual(0);
    expect(icons).toBeLessThan(yearly);
  });

  it("the start page stays uncached as before", () => {
    expect(block("= /index.html")).toContain('Cache-Control "no-cache, no-store, must-revalidate"');
  });
});
