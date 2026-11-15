/**
 * DEV-344 — on the screens the agent that builds a version in Riadiace centrum is the Staviteľ, never „AI Agent“.
 *
 * Director 10.11.2026: „Potrebujem zrozumiteľne nazvať agenta, ktorý pracuje v riadiacom centre.“ The rule reads every
 * text the screens say — string literals, template text, JSX text and the patterns tests find buttons by are parsed
 * out of each source file — so a new screen cannot bring the old name back. Comments are the code's own notes.
 */
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { ROLE_LABELS } from "@/components/cockpit/labels";

import { SRC, sources, texts, textsSaying } from "./screenTexts";

/** The old name in any form; „AI agentov“ / „AI agents“ are an app's own agents (a template feature), not ours. */
const OLD_NAME = /AI[ -]Agent|AI agent\b(?!ov|s)|\bagent(a|ovi)? stavby\b/;

describe("the build agent is the Staviteľ on every screen (DEV-344)", () => {
  it("no text of the cockpit's screens calls it AI Agent", () => {
    expect(textsSaying(OLD_NAME)).toEqual([]);
  });

  it("the rule reads texts out of real sources and sees each form of the old name", () => {
    expect(sources().length).toBeGreaterThan(100);
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
