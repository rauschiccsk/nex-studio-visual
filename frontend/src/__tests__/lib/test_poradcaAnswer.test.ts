/** Zobrazenie odpovede Poradcu (ICCINT-167). */

import { describe, expect, it } from "vitest";

import { answerForDisplay, formatCost, formatDuration, stepLabel } from "@/lib/poradcaAnswer";

describe("answerForDisplay", () => {
  it("turns the instruction block into a headed quote instead of dropping it", () => {
    const out = answerForDisplay("Agent stojí.\n<pokyn-pre-agenta>\nOprav test_login.\nPotom spusti testy.\n</pokyn-pre-agenta>");
    expect(out).toContain("**Pokyn pre agenta stavby:**");
    expect(out).toContain("> Oprav test_login.\n> Potom spusti testy.");
    expect(out).not.toContain("<pokyn-pre-agenta>");
  });

  it("does the same for a new-version request and leaves plain text alone", () => {
    expect(answerForDisplay("<poziadavka-na-novu-verziu>Export CSV.</poziadavka-na-novu-verziu>")).toContain(
      "**Požiadavka na novú verziu:**",
    );
    expect(answerForDisplay("Len odpoveď.")).toBe("Len odpoveď.");
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
    expect(formatCost(null)).toBe("cena nenastavená");
    expect(formatCost(0.4)).toMatch(/^0,40\s€$/);
  });
});
