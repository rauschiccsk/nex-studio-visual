/**
 * DEV-81 — on a computer (1280×800) the sidebar shows every tab without scrolling and keeps room for four more.
 * DEV-82 — the edges of the sidebar and of the task plan are handles: moved with the keyboard they change the width the
 * person sees, and the change goes to the server.
 *
 * Measured in a real browser — a test environment without layout cannot tell whether something scrolls.
 */

import { expect, test, type Page } from "@playwright/test";

import { serveApi } from "./api";
import { LAYOUT, registerFixtures, STORAGE } from "./fixtures";
import { settle } from "./layout";

registerFixtures();

/**
 * How many tabs the sidebar must hold without scrolling: the 17 it had when DEV-81 made it dense, and four more
 * (Director: „budeme pridávať ešte 3-4 nových záložiek“). Counted as a total, so a new tab uses up the room it was
 * meant for instead of lowering the bar — DEV-89's notes tab is the first of the four.
 */
const TABS_TO_HOLD = 17 + 4;

/** The server keeps what it is sent: a change of the layout comes back as it went. */
async function keepLayout(page: Page, saved: unknown[]) {
  let layout = { ...LAYOUT };
  await page.route("**/api/v1/user-layout", async (route) => {
    if (route.request().method() !== "PUT") return route.fallback();
    const changes = route.request().postDataJSON() as Record<string, unknown>;
    saved.push(changes);
    layout = { ...layout, ...changes };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(layout) });
  });
}

async function open(page: Page, url: string, saved?: unknown[]) {
  const unanswered = await serveApi(page);
  if (saved) await keepLayout(page, saved);
  await page.addInitScript((storage: Record<string, unknown>) => {
    for (const [key, value] of Object.entries(storage)) {
      window.localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
    }
  }, STORAGE);
  await page.goto(url);
  await settle(page);
  return unanswered;
}

test.beforeEach(() => {
  test.skip(test.info().project.name !== "pocitac", "the sidebar stands beside the content only on a computer");
});

test("the sidebar shows every tab at 1280×800 and holds the four tabs planned beyond DEV-81's 17", async ({ page }) => {
  const unanswered = await open(page, "/riadiace-centrum");

  const nav = await page.locator("aside nav").evaluate((el) => {
    // The tabs are the nav's own buttons; the selected project's line is a row of its own, not a tab.
    const items = [...el.querySelectorAll(":scope > button, :scope > a")];
    const last = el.lastElementChild!.getBoundingClientRect();
    const top = el.getBoundingClientRect().top;
    const padding = parseFloat(getComputedStyle(el).paddingBottom);
    return {
      scrolls: el.scrollHeight > el.clientHeight,
      free: el.clientHeight - (last.bottom - top) - padding,
      entry: items[0]!.getBoundingClientRect().height + parseFloat(getComputedStyle(items[1]!).marginTop || "0"),
      entries: items.length,
    };
  });

  expect(nav.scrolls, `the sidebar scrolls (${nav.entries} tabs)`).toBe(false);
  const missing = Math.max(0, TABS_TO_HOLD - nav.entries);
  expect(nav.free, `${nav.entries} tabs, room left ${nav.free} px for ${missing} more of ${nav.entry} px`).toBeGreaterThanOrEqual(
    missing * nav.entry,
  );
  expect(unanswered).toEqual([]);
});

test("the handles change the width of the sidebar and of the task plan, and the change goes to the server", async ({
  page,
}) => {
  const saved: unknown[] = [];
  const unanswered = await open(page, "/riadiace-centrum", saved);

  const sidebar = page.locator("aside").first();
  const before = (await sidebar.boundingBox())!.width;
  await page.getByRole("separator", { name: "Šírka bočného panela" }).focus();
  await page.keyboard.press("ArrowRight");
  await settle(page);
  expect((await sidebar.boundingBox())!.width).toBe(before + 16);

  const plan = page.getByRole("separator", { name: "Šírka Plánu úloh" });
  const planBefore = (await page.locator("aside").last().boundingBox())!.width;
  await plan.focus();
  await page.keyboard.press("ArrowRight"); // the plan lies right of its edge: moving the edge right narrows it
  await settle(page);
  expect((await page.locator("aside").last().boundingBox())!.width).toBe(planBefore - 16);

  expect(saved).toEqual([{ sidebar_width: 240 }, { plan_width: 304 }]);
  expect(unanswered).toEqual([]);
});
