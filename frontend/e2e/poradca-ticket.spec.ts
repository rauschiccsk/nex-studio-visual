/**
 * DEV-202 — „Založiť tiket“ under a long answer of Poradca opens the ticket's preview where it can be seen: the button
 * that founds the ticket is on the screen, not below its edge. 23.10.2026 the Director clicked under an answer of
 * 4 166 characters, the preview opened out of sight and no ticket came of it — „musel som scrolovať“.
 */

import { expect, test } from "@playwright/test";

import { override, serveApi } from "./api";
import { PORADCA_CONVERSATION, PORADCA_MESSAGES, registerFixtures, STORAGE } from "./fixtures";

registerFixtures();

const PROPOSAL = PORADCA_MESSAGES.find((m) => m.ticket_proposal);
if (!PROPOSAL) throw new Error("the fixtures carry no answer of Poradca proposing a ticket");
const LONG_ANSWER = {
  ...PROPOSAL,
  content:
    Array.from({ length: 40 }, (_, i) => `Odsek ${i + 1}: čo Poradca v kokpite zmeral a prečo to považuje za chybu.`).join(
      "\n\n",
    ) +
    "\n\n" +
    PROPOSAL.content,
};

test("the ticket's preview opens with its founding button on the screen", async ({ page }) => {
  const unanswered = await serveApi(page, [
    override("get", "/api/v1/poradca/conversations/{conversation_id}", {
      ...PORADCA_CONVERSATION,
      messages: [...PORADCA_MESSAGES.filter((m) => m !== PROPOSAL), LONG_ANSWER],
    }),
  ]);
  await page.addInitScript((storage: Record<string, unknown>) => {
    for (const [key, value] of Object.entries(storage)) {
      window.localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
    }
  }, STORAGE);

  await page.goto("/poradca");
  const open = page.getByRole("button", { name: "Založiť tiket", expanded: false });
  await open.click();

  const preview = page.getByRole("region", { name: "Návrh tiketu od Poradcu" });
  await expect(preview.getByRole("textbox", { name: "Názov tiketu" })).toHaveValue("Tlačidlo Nasadiť pri UAT nič nerobí");
  await expect(preview.getByRole("button", { name: "Založiť tiket" })).toBeInViewport();
  await expect(preview.getByRole("button", { name: "Zavrieť" })).toBeInViewport();
  expect(unanswered).toEqual([]);
});
