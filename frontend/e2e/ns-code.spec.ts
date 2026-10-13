/**
 * DEV-86 — a link to a line of a file (`/ns-code?path=…&line=…`) opens the file in the editor of VS Code, marks the
 * line, opens the file's folders in the tree, and the page with the editor still fits a phone, a tablet and a
 * computer. The general guard opens NS Code without a file; this one measures it with the editor drawn.
 */

import { expect, test } from "@playwright/test";

import { serveApi } from "./api";
import { registerFixtures, STORAGE } from "./fixtures";
import { problemsOn, settle } from "./layout";

registerFixtures();

test("a link to a line opens the file in the editor, marks the line and fits the screen", async ({
  page,
  hasTouch,
}) => {
  const unanswered = await serveApi(page);
  await page.addInitScript((storage: Record<string, unknown>) => {
    for (const [key, value] of Object.entries(storage)) {
      window.localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
    }
  }, STORAGE);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto("/ns-code?path=backend/main.py&line=9");
  await settle(page);

  const editor = page.locator(".monaco-editor");
  await expect(editor).toBeVisible({ timeout: 15_000 });
  await expect(editor.locator(".view-lines")).toContainText("def health()");
  await expect(editor.locator(".ns-code-line")).toHaveCount(1);
  await expect(page.getByRole("tab", { name: "main.py" })).toHaveAttribute("aria-selected", "true");

  // On a phone the tree is its own pane — the file's folder must be open there too.
  if ((page.viewportSize()?.width ?? 0) < 768) await page.getByRole("button", { name: "Súbory" }).click();
  await expect(page.getByRole("button", { name: /^backend/ })).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator('[aria-current="true"]', { hasText: "main.py" })).toBeVisible();

  if ((page.viewportSize()?.width ?? 0) < 768) await page.getByRole("button", { name: "Kód" }).click();
  await settle(page);
  expect(await problemsOn(page, hasTouch)).toEqual([]);

  // DEV-87 — the file side by side with the last commit (one column on a narrow screen) still fits.
  await page.getByRole("button", { name: "S posledným commitom" }).click();
  const diff = page.locator(".monaco-diff-editor");
  await expect(diff).toBeVisible({ timeout: 15_000 });
  await expect(diff).toContainText("def health()");
  await settle(page);
  expect(await problemsOn(page, hasTouch)).toEqual([]);

  expect(errors).toEqual([]);
  expect(unanswered).toEqual([]);
});
