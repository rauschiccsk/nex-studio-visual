/**
 * DEV-332 — no page of the cockpit narrows itself to a fixed column: the screen is used.
 *
 * 09.11.2026 the Director, on the UAT page of NEX Websites: „Nevyužívame efektívne plochu obrazovky … v strede je
 * textbox a z obidvoch strán sú nevyužité priestory a my potom lámeme text ako napríklad ICC s.r.o. do dvoch riadkov a
 * pritom priestor máme, chcel by som aby obrazovka bola využitá aspoň na 90%“. On his screen (1 913 px) the page took a
 * centred column of 1 024 px — 14 pages set such a width of their own (`mx-auto max-w-5xl` and the like). The guard is
 * a rule, not a list: every source file is read, and a wide max-width class is allowed only where a reason is written
 * below — a window over the page (a dialog) has a width of its own. The browser measures the rest (e2e/width.spec.ts).
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** A width that narrows a page to a column: 42 rem and more, or a screen size. Small limits (a hint's `max-w-md`) stay. */
const NARROW = /\bmax-w-(2xl|3xl|4xl|5xl|6xl|7xl|screen-[a-z0-9]+)\b/;

/** Where a wide max-width is right, and why. */
const ALLOWED: Record<string, string> = {
  "components/deploy/AdoptInstanceDialog.tsx": "dialóg nad stránkou — okno má vlastnú šírku, stránka pod ním nie",
};

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "__tests__" ? [] : sources(path);
    return /\.tsx?$/.test(name) ? [relative(SRC, path)] : [];
  });
}

const narrowing = sources(SRC).filter((file) => NARROW.test(readFileSync(join(SRC, file), "utf-8")));

describe("the cockpit uses the screen (DEV-332)", () => {
  it("no page narrows itself to a column of a fixed width", () => {
    expect(narrowing.filter((file) => !(file in ALLOWED))).toEqual([]);
  });

  it("every allowed exception still exists and still needs its reason", () => {
    expect(Object.keys(ALLOWED).filter((file) => !narrowing.includes(file))).toEqual([]);
  });
});
