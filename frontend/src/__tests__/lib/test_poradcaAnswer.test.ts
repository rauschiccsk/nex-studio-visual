/** Zobrazenie odpovede Poradcu (ICCINT-167). */

import { describe, expect, it } from "vitest";

import {
  SHORT_ANSWER_MAX,
  answerForDisplay,
  choiceCard,
  formatCost,
  formatDuration,
  longAnswer,
  stepLabel,
} from "@/lib/poradcaAnswer";

describe("answerForDisplay", () => {
  it("turns the instruction block into a headed quote instead of dropping it", () => {
    const out = answerForDisplay("Agent stojí.\n<pokyn-pre-agenta>\nOprav test_login.\nPotom spusti testy.\n</pokyn-pre-agenta>");
    expect(out).toContain("**Pokyn pre agenta stavby:**");
    expect(out).toContain("> Oprav test_login.\n> Potom spusti testy.");
    expect(out).not.toContain("<pokyn-pre-agenta>");
  });

  it("does the same for a request to the Zásobník and leaves plain text alone", () => {
    expect(answerForDisplay("<poziadavka-do-zasobnika>Export CSV.</poziadavka-do-zasobnika>")).toContain(
      "**Požiadavka do Zásobníka:**",
    );
    expect(answerForDisplay("Len odpoveď.")).toBe("Len odpoveď.");
  });

  // DEV-83: a marked choice becomes a link the answer draws as a badge — the tag itself never reaches the screen.
  it("turns a marked choice into a link that names its card", () => {
    const out = answerForDisplay('Na karte 3 vyber <volba karta="3">Zhasnúť  hneď,\nkeď sa obnoví</volba>. A <volba>Áno</volba>.');
    expect(out).toBe("Na karte 3 vyber [Zhasnúť hneď, keď sa obnoví](#volba-3). A [Áno](#volba).");
    expect(answerForDisplay('<volba karta="1">Voľba [A] a \\ B</volba>')).toBe("[Voľba \\[A\\] a \\\\ B](#volba-1)");
    expect(choiceCard("#volba-3")).toBe(3);
    expect(choiceCard("#volba")).toBeNull();
  });

  it("DEV-29: an answer from before the change shows its request as one for the Zásobník, never a new version", () => {
    const out = answerForDisplay("<poziadavka-na-novu-verziu>Dobropisy.</poziadavka-na-novu-verziu>");
    expect(out).toContain("**Požiadavka do Zásobníka:**");
    expect(out).not.toContain("novú verziu");
  });
});

describe("stepLabel", () => {
  it("names what Poradca is doing", () => {
    expect(stepLabel("Read", "backend/app.py")).toBe("Čítam — backend/app.py");
    expect(stepLabel("databaza_uat", "UAT andros")).toBe("Pýtam sa databázy UAT — UAT andros");
    expect(stepLabel("stavba", "")).toBe("Pozerám stav stavby");
  });

  it("keeps an unknown tool's own name", () => {
    expect(stepLabel("novy_nastroj", "x")).toBe("novy_nastroj — x");
  });
});

describe("formats", () => {
  it("duration and cost", () => {
    expect(formatDuration(42)).toBe("42 s");
    expect(formatDuration(80)).toBe("1 min 20 s");
    expect(formatDuration(120)).toBe("2 min");
    expect(formatDuration(null)).toBe("");
    expect(formatCost(null)).toBe("cena sa zatiaľ nedá vyčísliť");
    expect(formatCost(0.4)).toMatch(/^0,40\s€$/);
  });
});

// DEV-224 — green is what to pick: a short answer to the agent. One line of at most SHORT_ANSWER_MAX characters stays a
// choice; anything longer is drawn and inserted as the answer for the agent, with the purple button.
describe("longAnswer (DEV-224)", () => {
  it("draws the edge where a choice stops being one", () => {
    const at = "a".repeat(SHORT_ANSWER_MAX);
    expect(longAnswer(`<volba>${at}</volba>`)).toBeNull();
    expect(longAnswer(`<volba>${at}b</volba>`)).toBe(`${at}b`);
    expect(longAnswer("<volba>Áno,\nsúhlasím</volba>")).toBe("Áno,\nsúhlasím");
  });

  it("never takes a card's option", () => {
    expect(longAnswer(`<volba karta="3">${"x".repeat(SHORT_ANSWER_MAX + 1)}</volba>`)).toBeNull();
  });

  it("shows a long answer as a quoted block, a short one as a choice", () => {
    const long = "b".repeat(SHORT_ANSWER_MAX + 1);
    expect(answerForDisplay(`Odpovedz <volba>${long}</volba>`)).toBe(`Odpovedz \n**Odpoveď pre agenta:**\n\n> ${long}`);
    expect(answerForDisplay("Odpovedz <volba>Áno</volba>")).toBe("Odpovedz [Áno](#volba)");
  });
});
