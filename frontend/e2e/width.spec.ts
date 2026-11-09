/**
 * DEV-332 — on a wide screen every page of the cockpit uses at least 90 % of the space beside the menu.
 *
 * The Director's screen is 1 913 px wide; the layout guard (layout.spec.ts) measures at 1 280 px, where a column of
 * 1 024 px fills almost everything — so a page that left a third of the Director's screen empty passed it. Here every
 * page the router has (`routes.ts`) is opened at 1 920 px and the width its content takes is measured in `main`.
 */
import { expect, test } from "@playwright/test";

import { serveApi } from "./api";
import { registerFixtures, STORAGE } from "./fixtures";
import { settle } from "./layout";
import { pages } from "./routes";

registerFixtures();

/** The share of the space beside the menu the page's content must take (Director 09.11.2026: „aspoň na 90%“). */
const SHARE = 0.9;

test.use({ viewport: { width: 1920, height: 1080 } });

for (const page of pages().filter((p) => p.signedIn)) {
  test(`${page.pattern} uses the wide screen`, async ({ page: browser }, info) => {
    test.skip(info.project.name !== "pocitac", "one wide screen is measured once");
    await serveApi(browser, []);
    await browser.addInitScript((storage: Record<string, unknown>) => {
      for (const [key, value] of Object.entries(storage)) {
        window.localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
      }
    }, STORAGE);
    await browser.goto(page.url);
    await settle(browser);

    const measured = await browser.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      const box = main.getBoundingClientRect();
      const style = getComputedStyle(main);
      const inner = box.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      let left = Infinity;
      let right = -Infinity;
      for (const el of Array.from(main.querySelectorAll("*"))) {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        if (r.width === 0 || r.height === 0 || s.visibility === "hidden" || s.position === "fixed") continue;
        left = Math.min(left, Math.max(r.left, box.left));
        right = Math.max(right, Math.min(r.right, box.right));
      }
      return { used: Math.round(right - left), inner: Math.round(inner) };
    });

    expect(measured, "the page has no <main>").not.toBeNull();
    const { used, inner } = measured!;
    expect(used / inner, `${page.url}: the content takes ${used} of ${inner} px`).toBeGreaterThanOrEqual(SHARE);
  });
}
