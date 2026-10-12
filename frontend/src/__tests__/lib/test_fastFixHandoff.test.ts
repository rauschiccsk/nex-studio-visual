/** DEV-91 — a change that does not work in UAT becomes the text of a fast fix the manager starts himself. */

import { beforeEach, describe, expect, it } from "vitest";

import { fastFixFromUat, fastFixText, forgetFastFixOrigin, handOffFastFix } from "@/lib/fastFixHandoff";

const CHANGE = {
  version: "1.8.0",
  customer: "MÁGERSTAV",
  title: "Detail faktúry v Chrome 109",
  steps: ["Otvor zoznam faktúr.", "Klikni na faktúru EMOS."],
  expected: ["Detail sa otvorí aj s PDF."],
  note: "  Ukáže sa biela stránka.  ",
};

beforeEach(() => window.localStorage.clear());

describe("fastFixHandoff", () => {
  it("writes the brief from the change and what happened instead", () => {
    expect(fastFixText(CHANGE)).toBe(
      [
        "V UAT u zákazníka MÁGERSTAV vo verzii 1.8.0 nefunguje: Detail faktúry v Chrome 109.",
        "Kroky:",
        "1. Otvor zoznam faktúr.",
        "2. Klikni na faktúru EMOS.",
        "Očakávané: Detail sa otvorí aj s PDF.",
        "Čo sa stalo: Ukáže sa biela stránka.",
        "Oprav to tak, aby sa stalo očakávané.",
      ].join("\n"),
    );
  });

  it("puts the text under his draft, marks where it came from and points at the open dialog", () => {
    window.localStorage.setItem("nex.draft.rychla-oprava.nex-inbox", "Môj rozpísaný text.");

    const where = handOffFastFix("nex-inbox", "Pripravené zadanie.");

    expect(where).toBe("/projects/nex-inbox?rychla-oprava=1");
    expect(window.localStorage.getItem("nex.draft.rychla-oprava.nex-inbox")).toBe(
      "Môj rozpísaný text.\n\nPripravené zadanie.",
    );
    expect(fastFixFromUat("nex-inbox")).toBe(true);
    forgetFastFixOrigin("nex-inbox");
    expect(fastFixFromUat("nex-inbox")).toBe(false);
  });
});
