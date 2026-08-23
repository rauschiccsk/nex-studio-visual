/**
 * Every text surface in the cockpit DECIDES about spellcheck. None of them leaves it to chance (ICCINT-11).
 *
 * The first version of this rule switched spellcheck OFF everywhere, on the theory that the browser has no
 * Slovak dictionary so every squiggle is noise. The Director disproved it while accepting the work: he typed
 * a MISSPELLED word and it was not flagged either — we had thrown the feature away, not fixed it. The real
 * cause was on his machine: Chrome DOES know Slovak, but in `chrome://settings/languages` the Slovak
 * dictionary was off and English (US) was on, so Slovak prose was being checked against an ENGLISH
 * dictionary. That is why every correct word was underlined.
 *
 * So the rule is by KIND OF FIELD, which is still one rule, applied uniformly:
 *   - you write SENTENCES there (a description, a message to the agent, a document body, a comment)
 *     → spellcheck ON, ``lang="sk"``, and the browser needs its Slovak dictionary enabled;
 *   - it holds an IDENTIFIER (a slug, a port, a version, a filename, a price, a secret, a search box)
 *     → spellcheck OFF: a dictionary has nothing to say about it and would flag every value.
 *
 * This scans the source rather than rendering, because the point is COVERAGE — a rule that holds on the
 * screens somebody remembered to test is not a rule. What it pins is that the decision was MADE: a new text
 * box with no ``spellCheck`` prop at all turns this red on the commit that adds it, and whoever adds it has
 * to say which kind of field it is.
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
  it("is decided explicitly on every text surface in the cockpit", () => {
    const files = tsxFiles(SRC);

    expect(files.length).toBeGreaterThan(20); // the glob itself must not silently match nothing

    const offenders: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      for (const tag of openingTags(source)) {
        // Either value is a legitimate answer; NOT ANSWERING is not.
        if (tag.includes("spellCheck={true}") || tag.includes("spellCheck={false}")) continue;
        const type = /type="([a-z]+)"/.exec(tag)?.[1];
        if (type && NON_TEXT_TYPES.has(type)) continue;
        const line = source.slice(0, source.indexOf(tag)).split("\n").length;
        offenders.push(`${relative(SRC, file)}:${line}`);
      }
    }

    expect(
      offenders,
      `text surfaces that never decided about spellcheck:\n${offenders.join("\n")}`,
    ).toEqual([]);
  });
});
