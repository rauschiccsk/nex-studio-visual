/**
 * One rule for the whole cockpit: no red squiggles under Slovak (ICCINT-11).
 *
 * The UI is written in Slovak and browsers do not ship a Slovak dictionary by default, so the
 * spellchecker underlines every correct word. The squiggles carry no information — they are noise over
 * text that is right — and the Director hit them writing to the AI Agent on 22.08.2026.
 *
 * Before this, 20 of 55 text surfaces had it switched off and the rest did not: the same product
 * behaved differently depending on which screen you were on, which is worse than either choice made
 * consistently.
 *
 * This scans the source rather than rendering, because the point is COVERAGE — a rule that holds on
 * the screens somebody remembered to test is not a rule. A new text box without ``spellCheck={false}``
 * turns this red on the commit that adds it.
 */
import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative, resolve } from "node:path";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** Types that hold no typed text — a spellchecker has nothing to check and the attribute would be clutter. */
const NON_TEXT_TYPES = new Set([
  "checkbox",
  "radio",
  "file",
  "range",
  "color",
  "submit",
  "button",
  "number",
  "date",
]);

/** Opening tags of every ``<input>`` / ``<textarea>`` in *source*, brace-aware so JSX expressions
 *  containing ``>`` do not end the tag early. */
function openingTags(source: string): string[] {
  const tags: string[] = [];
  const re = /<(input|textarea)\b/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    let depth = 0;
    let j = re.lastIndex;
    while (j < source.length) {
      const c = source[j];
      if (c === "{") depth += 1;
      else if (c === "}") depth -= 1;
      else if (c === ">" && depth === 0) break;
      j += 1;
    }
    tags.push(source.slice(m.index, j));
  }
  return tags;
}

/** Every ``.tsx`` under *dir*, tests excluded. Hand-rolled rather than a glob helper so the sweep does
 *  not depend on which Node version the CI runner happens to have. */
function tsxFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__tests__" || entry.name === "node_modules") continue;
      found.push(...tsxFiles(full));
    } else if (entry.name.endsWith(".tsx")) {
      found.push(full);
    }
  }
  return found;
}

describe("spellcheck", () => {
  it("is switched off on every text surface in the cockpit", () => {
    const files = tsxFiles(SRC);

    expect(files.length).toBeGreaterThan(20); // the glob itself must not silently match nothing

    const offenders: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const tag of openingTags(source)) {
        if (tag.includes("spellCheck")) continue;
        const type = /type="([a-z]+)"/.exec(tag)?.[1];
        if (type && NON_TEXT_TYPES.has(type)) continue;
        const line = source.slice(0, source.indexOf(tag)).split("\n").length;
        offenders.push(`${relative(SRC, file)}:${line}`);
      }
    }

    expect(offenders, `text surfaces without spellCheck={false}:\n${offenders.join("\n")}`).toEqual(
      [],
    );
  });
});
