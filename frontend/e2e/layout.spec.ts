/**
 * DEV-62 — every page of the cockpit fits a phone (375 px), a tablet (768 px) and a computer (1280 px).
 *
 * The widths are the Playwright projects in `playwright.config.ts`; the pages come from the router
 * (`routes.ts`), the data from `fixtures.ts`, and what "fits" means is `layout.ts`. Every tab of a page is opened
 * and measured too.
 */

import { expect, test } from "@playwright/test";

import { serveApi } from "./api";
import { registerFixtures, STORAGE } from "./fixtures";
import { problemsOn, settle, type Problem } from "./layout";
import { pages } from "./routes";

registerFixtures();

const report = (problems: Problem[]): string[] => problems.map((p) => `${p.rule}: ${p.what} — ${p.detail}`);

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

    await browser.goto(page.url);
    await settle(browser);
    expect(new URL(browser.url()).pathname, "the page redirected elsewhere — its fixtures do not let it open").toBe(
      page.url,
    );

    const found: Record<string, string[]> = { "": report(await problemsOn(browser, hasTouch)) };
    const tabs = browser.locator('main [role="tab"]:visible');
    for (let i = 0; i < (await tabs.count()); i++) {
      const tab = tabs.nth(i);
      const name = ((await tab.innerText()) || `tab ${i + 1}`).trim();
      await tab.click();
      await settle(browser);
      found[`tab „${name}“`] = report(await problemsOn(browser, hasTouch));
    }

    test.info().annotations.push({ type: "measured", description: Object.keys(found).map((k) => k || "page").join(", ") });
    expect.soft(unanswered, "API calls without a fixture — the page was measured half empty").toEqual([]);
    expect.soft(errors, "the page threw").toEqual([]);
    for (const [where, problems] of Object.entries(found)) {
      expect.soft(problems, `${page.url} ${where}`.trim()).toEqual([]);
    }
  });
}
