/**
 * DEV-390 — the box of Riadiace centrum shows its text on as many rows after a visit to Poradca as before it.
 *
 * 15.11.2026 the Director: an answer of Poradca put into Riadiace centrum showed on several rows, but after switching
 * to Poradca and back the box „zúži sa na 2 riadky, čo je vlastne zle čitateľné“. Measured in a real browser — jsdom
 * lays nothing out, so a unit test cannot see a height.
 */

import { expect, test, type Page } from "@playwright/test";

import { override, serveApi, type Override } from "./api";
import { PIPELINE, registerFixtures, STORAGE, VERSION } from "./fixtures";

registerFixtures();

const INSTRUCTION = Array.from(
  { length: 8 },
  (_, i) => `${i + 1}. Bod pokynu od Poradcu, ktorý má Staviteľ zapracovať do ďalšej úlohy.`,
).join("\n");

/** The build waits on the Staviteľ's question — the answer box of the recovery bar takes the Manažér's text. */
const WAITS_ON_A_QUESTION: Override[] = [
  override("get", "/api/v1/pipeline/{version_id}", {
    ...PIPELINE,
    state: {
      ...PIPELINE.state!,
      status: "blocked",
      block_reason: "agent_question",
      next_action: "Staviteľ sa pýta: ktorý formát dátumu má mať výpis faktúr?",
    },
    available_actions: ["pozastavit"],
  }),
];

/** Every box of Riadiace centrum that takes Poradca's instruction (poradcaHandoff → inputOwner): the conversation,
 *  a decision card's note during a consultation (the fixtures' build consults on the card „opakovanie“) and the
 *  answer to the Staviteľ's question. */
const BOXES: { surface: string; key: string; overrides: Override[] }[] = [
  { surface: "rozhovor", key: `nex.draft.rozhovor.${VERSION.id}`, overrides: [] },
  { surface: "karta", key: `nex.draft.karta.opakovanie.${VERSION.id}`, overrides: [] },
  { surface: "odpoved", key: `nex.draft.odpoved.${VERSION.id}`, overrides: WAITS_ON_A_QUESTION },
];

async function rows(page: Page, surface: string): Promise<number> {
  return page.locator(`textarea[data-draft="${surface}"]`).evaluate((el) => {
    const line = Number.parseFloat(getComputedStyle(el).lineHeight);
    return Math.round(el.clientHeight / line);
  });
}

for (const { surface, key, overrides } of BOXES) {
  test(`the ${surface} box keeps its rows after a visit to Poradca`, async ({ page }, testInfo) => {
    // The way back goes through the side menu as the Director's did; on a phone and a tablet it is folded away. The
    // height is measured by one hook for every width (useAutoGrowTextarea).
    test.skip(testInfo.project.name !== "pocitac", "the side menu is folded away on narrower screens");
    await serveApi(page, overrides);
    await page.addInitScript(
      ({ storage, draft }: { storage: Record<string, unknown>; draft: [string, string] }) => {
        for (const [name, value] of Object.entries(storage)) {
          window.localStorage.setItem(name, typeof value === "string" ? value : JSON.stringify(value));
        }
        if (!window.sessionStorage.getItem("dev390.seeded")) {
          window.sessionStorage.setItem("dev390.seeded", "1");
          window.localStorage.setItem(draft[0], draft[1]);
        }
      },
      { storage: STORAGE, draft: [key, INSTRUCTION] as [string, string] },
    );
    const box = page.locator(`textarea[data-draft="${surface}"]`);

    await page.goto("/riadiace-centrum");
    await expect(box).toHaveValue(INSTRUCTION);
    await expect.poll(() => rows(page, surface)).toBeGreaterThanOrEqual(8);
    const before = await rows(page, surface);

    await page.getByRole("button", { name: "Poradca", exact: true }).click();
    await expect(page).toHaveURL(/\/poradca/);
    await page.getByRole("button", { name: /^Riadiace centrum/ }).click();
    await expect(box).toHaveValue(INSTRUCTION);

    await expect.poll(() => rows(page, surface)).toBe(before);
  });
}
