/**
 * DEV-486 — the cards one by one have one owner: the summary of the fast path draws them when there is no summary or
 * when Poradca did not finish it, and never beside a summary.
 *
 * 21.11.2026 Riadiace centrum drew the summary and, under it, the same questions one by one — the Director: „Ak je
 * zapnutá rýchla cesta, potom nechcem mať súčasne na obrazovke aj otázky po jednom - je to mätúce.“ The page rendered
 * both side by side; a screen that renders the cards itself would bring the two back.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..", "..");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "__tests__" ? [] : sources(path);
    return /\.tsx$/.test(name) ? [path] : [];
  });
}

describe("the cards one by one are drawn only by the summary (DEV-486)", () => {
  it("no screen but BatchReviewSummary renders DecisionCardsBar", () => {
    const drawing = sources(SRC)
      .filter((file) => /<DecisionCardsBar\b/.test(readFileSync(file, "utf-8")))
      .map((file) => relative(SRC, file));

    expect(drawing).toEqual(["components/controlCenter/BatchReviewSummary.tsx"]);
  });
});
