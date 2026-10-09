import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * DEV-21 — the page tells the browser it is an installable app: without the manifest link no browser offers the
 * installation, without theme-color the window flashes white on start, and the icon is what the Director clicks.
 */

const FRONTEND = resolve(__dirname, "../../..");
const INDEX = readFileSync(resolve(FRONTEND, "index.html"), "utf-8");
const MANIFEST = JSON.parse(readFileSync(resolve(FRONTEND, "public/manifest.webmanifest"), "utf-8"));

describe("the cockpit is an installable app", () => {
  it("index.html links the manifest, the icon and the dark theme colour", () => {
    expect(INDEX).toContain('<link rel="manifest" href="/manifest.webmanifest" />');
    expect(INDEX).toContain('<link rel="icon" href="/icon.svg" type="image/svg+xml" />');
    expect(INDEX).toContain('<link rel="apple-touch-icon" href="/icon-192.png" />');
    expect(INDEX).toContain('<meta name="theme-color" content="#0e1116" />');
  });

  it("the manifest names the cockpit and opens it in its own window", () => {
    expect(MANIFEST.name).toBe("NEX Studio Visual");
    expect(MANIFEST.display).toBe("standalone");
    expect(MANIFEST.start_url).toBe("/");
    expect(MANIFEST.theme_color).toBe("#0e1116");
  });
});
