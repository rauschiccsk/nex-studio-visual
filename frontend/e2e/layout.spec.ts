/**
 * DEV-62 — every page of the cockpit fits a phone (375 px), a tablet (768 px) and a computer (1280 px).
 *
 * The widths are the Playwright projects in `playwright.config.ts`; the pages come from the router
 * (`routes.ts`), the data from `fixtures.ts`, and what "fits" means is `layout.ts`. Every tab of a page is opened
 * and measured, and in every tab every dialog and every folded part that says so (`aria-haspopup="dialog"`,
 * `aria-expanded`), each on a freshly loaded page.
 */

import { expect, test, type Page as Browser } from "@playwright/test";

import { serveApi } from "./api";
import { registerFixtures, STORAGE } from "./fixtures";
import { OPENERS, problemsOn, settle, type Problem } from "./layout";
import { pages } from "./routes";

registerFixtures();

const TABS = 'main [role="tab"]:visible';

const report = (problems: Problem[]): string[] => problems.map((p) => `${p.rule}: ${p.what} — ${p.detail}`);

const nameOf = async (browser: Browser, selector: string, i: number): Promise<string> => {
  const el = browser.locator(selector).nth(i);
  return ((await el.getAttribute("aria-label")) || (await el.innerText()) || `#${i + 1}`).trim().slice(0, 40);
};

for (const page of pages()) {
  test(`${page.pattern} fits the screen`, async ({ page: browser, hasTouch }) => {
    const unanswered = await serveApi(browser);
    if (page.signedIn) {
      await browser.addInitScript((storage: Record<string, unknown>) => {
        for (const [key, value] of Object.entries(storage)) {
          window.localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
        }
      }, STORAGE);
    }
    const errors: string[] = [];
    browser.on("pageerror", (e) => errors.push(e.message));

    /** Load the page afresh and open its tab number `tab` (-1 = as it opens). */
    const reach = async (tab: number) => {
      await browser.goto(page.url);
      await settle(browser);
      if (tab >= 0) {
        await browser.locator(TABS).nth(tab).click();
        await settle(browser);
      }
    };

    await reach(-1);
    expect(new URL(browser.url()).pathname, "the page redirected elsewhere — its fixtures do not let it open").toBe(
      page.url,
    );

    const found: Record<string, string[]> = {};
    const tabCount = await browser.locator(TABS).count();
    const tabNames = await Promise.all(Array.from({ length: tabCount }, (_, i) => nameOf(browser, TABS, i)));
    for (let tab = -1; tab < tabCount; tab++) {
      const state = tab < 0 ? "" : `tab „${tabNames[tab]}“`;
      await reach(tab);
      found[state] = report(await problemsOn(browser, hasTouch));

      const openers = await browser.locator(OPENERS).count();
      for (let i = 0; i < openers; i++) {
        await reach(tab);
        const name = await nameOf(browser, OPENERS, i);
        const opener = browser.locator(OPENERS).nth(i);
        // Marked before the click: on a phone the opened part may take the opener's place.
        await opener.evaluate((el) => el.setAttribute("data-guard-opener", ""));
        await opener.click();
        await settle(browser);
        const opened =
          (await browser.locator('[role="dialog"]:visible').count()) > 0 ||
          (await browser.locator('[data-guard-opener][aria-expanded="true"]').count()) > 0;
        expect.soft(opened, `„${name}“ says it opens something, and nothing opened`).toBe(true);
        found[`${state} opened „${name}“`.trim()] = report(await problemsOn(browser, hasTouch));
      }
    }

    test.info().annotations.push({ type: "measured", description: Object.keys(found).map((k) => k || "page").join(", ") });
    expect.soft(unanswered, "API calls without a fixture — the page was measured half empty").toEqual([]);
    expect.soft(errors, "the page threw").toEqual([]);
    for (const [where, problems] of Object.entries(found)) {
      expect.soft(problems, `${page.url} ${where}`.trim()).toEqual([]);
    }
  });
}
