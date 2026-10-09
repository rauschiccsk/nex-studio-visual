/**
 * Every text area in the cockpit DECIDES whether its half-written text survives leaving the screen (DEV-32).
 *
 * ICCINT-30 (26.08.2026) taught the fields of that day to keep a draft (useDraft), one by one. Poradca came later,
 * nobody wired it, and the Director lost a half-written question again (DEV-24, 08.10.2026). Measured 09.10.2026:
 * 16 files carry a text area and only 6 of them kept a draft — among the rest the Zadanie on the version page,
 * the Zadanie of a new version and the Knowledge Base editor.
 *
 * So the rule is a decision per field, like spellcheck (ICCINT-11): every ``<textarea>`` says either
 * ``data-draft="<surface>"`` — and its file keeps it with ``useDraft`` — or ``data-no-draft="<reason>"``, the
 * reason a person can read (a password must never land in the browser's storage). A new text area that says
 * neither turns this red on the commit that adds it. It scans the source because the point is coverage.
 */
import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative, resolve } from "node:path";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** Opening tags of every ``<textarea>`` in *source*, brace-aware so ``=>`` inside a prop does not end the tag. */
function textareaTags(source: string): string[] {
  const tags: string[] = [];
  const re = /<textarea\b/g;
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

const files = tsxFiles(SRC)
  .map((path) => ({ path: relative(SRC, path), source: readFileSync(path, "utf-8") }))
  .filter((f) => textareaTags(f.source).length > 0);

describe("DEV-32 — a half-written text survives leaving the screen, or the field says why not", () => {
  it("finds the text areas it is about (the sweep itself works)", () => {
    expect(files.length).toBeGreaterThanOrEqual(16);
  });

  it("every text area decides: data-draft, or data-no-draft with a reason", () => {
    const undecided: string[] = [];
    for (const f of files) {
      for (const tag of textareaTags(f.source)) {
        const draft = /\bdata-draft=/.test(tag);
        const noDraft = /\bdata-no-draft="[^"]{10,}"/.test(tag);
        if (draft === noDraft) undecided.push(`${f.path}: ${tag.replace(/\s+/g, " ").slice(0, 90)}…`);
      }
    }
    expect(undecided).toEqual([]);
  });

  it("a file that promises drafts keeps one per text area with useDraft", () => {
    const broken: string[] = [];
    for (const f of files) {
      const promised = textareaTags(f.source).filter((t) => /\bdata-draft=/.test(t)).length;
      const kept = (f.source.match(/\buseDraft\(/g) ?? []).length;
      if (promised > kept) broken.push(`${f.path}: ${promised} text areas promise a draft, ${kept} useDraft`);
    }
    expect(broken).toEqual([]);
  });

  it("the fields where the Director loses real work keep their draft", () => {
    const drafted = (path: string) =>
      textareaTags(files.find((f) => f.path === path)?.source ?? "").some((t) => /\bdata-draft=/.test(t));
    expect(drafted("pages/VersionDetailPage.tsx")).toBe(true);
    expect(drafted("pages/NewVersionPage.tsx")).toBe(true);
    expect(drafted("pages/KnowledgeBasePage.tsx")).toBe(true);
    expect(drafted("pages/PoradcaPage.tsx")).toBe(true);
  });

  it("a secret is never kept in the browser", () => {
    const tags = textareaTags(files.find((f) => f.path === "pages/CredentialsPage.tsx")?.source ?? "");
    expect(tags.length).toBeGreaterThan(0);
    expect(tags.every((t) => /\bdata-no-draft=/.test(t))).toBe(true);
  });
});
