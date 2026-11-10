/**
 * DEV-344 — on the screens the agent that builds a version in Riadiace centrum is the Staviteľ, never „AI Agent“.
 *
 * Director 10.11.2026: „Potrebujem zrozumiteľne nazvať agenta, ktorý pracuje v riadiacom centre.“ The rule reads every
 * text the screens say — string literals, template text, JSX text and the patterns tests find buttons by are parsed
 * out of each source file — so a new screen cannot bring the old name back. Comments are the code's own notes.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { ROLE_LABELS } from "@/components/cockpit/labels";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** The old name in any form; „AI agentov“ / „AI agents“ are an app's own agents (a template feature), not ours. */
const OLD_NAME = /AI[ -]Agent|AI agent\b(?!ov|s)|\bagent(a|ovi)? stavby\b/;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "__tests__" ? [] : sources(path);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith(".generated.ts") ? [path] : [];
  });
}

/** Every text a source file says to a person. */
function texts(file: string): string[] {
  const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node) ||
      ts.isJsxText(node)
    ) {
      found.push(node.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

describe("the build agent is the Staviteľ on every screen (DEV-344)", () => {
  it("no text of the cockpit's screens calls it AI Agent", () => {
    const said = sources(SRC).flatMap((file) =>
      texts(file)
        .filter((text) => OLD_NAME.test(text))
        .map((text) => `${relative(SRC, file)}: ${text.trim().slice(0, 80)}`),
    );
    expect(said).toEqual([]);
  });

  it("the rule reads texts out of real sources and sees each form of the old name", () => {
    expect(sources(SRC).length).toBeGreaterThan(100);
    expect(texts(join(SRC, "components/cockpit/labels.ts"))).toContain("Staviteľ");
    for (const sentence of ["AI Agent pracuje", "otázka pre AI Agenta", "Napíš AI Agentovi", "agent stavby stojí"]) {
      expect(OLD_NAME.test(sentence)).toBe(true);
    }
    for (const sentence of ["Aplikácia používa AI agentov", "Staviteľ pracuje"]) {
      expect(OLD_NAME.test(sentence)).toBe(false);
    }
  });

  it("the author of a build's messages is shown as the Staviteľ", () => {
    expect(ROLE_LABELS.ai_agent).toBe("Staviteľ");
  });
});
