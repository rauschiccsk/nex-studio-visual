/**
 * DEV-103 — the question the review-with-Poradca button composes from a verification decision card.
 *
 * Director 13.10.2026: he copied the card's heading into Poradca by hand and wrote under it a request to check whether
 * the finding is justified and how to solve it (REVIEW_REQUEST). The question now carries the card itself — the
 * heading, the round, the explanation with the critic's word, the Auditor's findings with the proposed fix (the
 * technical detail) and the options — and ends with his sentence. A card on NEX Inbox 1.8.0 held about 16 500
 * characters against Poradca's 20 000, so the question fits the limit the backend reports.
 */

import { describe, it, expect, beforeEach } from "vitest";

import {
  REVIEW_PARAM,
  REVIEW_REQUEST,
  handOffReview,
  reviewAddress,
  reviewQuestion,
  takeReview,
  type ReviewCard,
} from "@/lib/poradcaReview";

const CARD: ReviewCard = {
  position: 1,
  total: 1,
  round: 4,
  question: "Verifikácia našla blokujúcu chybu. Ako chceš pokračovať?",
  explanation:
    "Kontrola CI na GitHube je červená — RELEASE_NOTES generated-file check.\n\n" +
    "Navrhnutú opravu nezávisle PREVERIL kritik (accept — je vynútená konštrukciou). Súbor sa pregeneruje.",
  technicalDetail:
    "## Verifikácia FAIL — oprav podľa nálezov Auditora\n\n" +
    "Spusti npm run generate-release-notes a zapíš frontend/src/lib/release-notes.ts.\n\n" +
    "- CI aee11b5: RELEASE_NOTES generated-file check zlyhal",
  options: [
    { label: "Spustiť pripravenú opravu", recommended: true },
    { label: "Usmerniť opravu (napíš vlastný pokyn)" },
    { label: "Zatiaľ podržať (rozhodnem neskôr)" },
  ],
};

beforeEach(() => window.localStorage.clear());

describe("reviewQuestion — the card as a question", () => {
  it("carries the whole card and ends with the Director's request", () => {
    expect(reviewQuestion(CARD, 20_000).split("\n")).toEqual([
      "Rozhodnutie 1 z 1 — Verifikácia našla blokujúcu chybu. Ako chceš pokračovať?",
      "Kolo opráv 4.",
      "",
      "Kontrola CI na GitHube je červená — RELEASE_NOTES generated-file check.",
      "",
      "Navrhnutú opravu nezávisle PREVERIL kritik (accept — je vynútená konštrukciou). Súbor sa pregeneruje.",
      "",
      "Technický detail:",
      "## Verifikácia FAIL — oprav podľa nálezov Auditora",
      "",
      "Spusti npm run generate-release-notes a zapíš frontend/src/lib/release-notes.ts.",
      "",
      "- CI aee11b5: RELEASE_NOTES generated-file check zlyhal",
      "",
      "Možnosti na karte: Spustiť pripravenú opravu (odporúčané) · Usmerniť opravu (napíš vlastný pokyn) · " +
        "Zatiaľ podržať (rozhodnem neskôr)",
      "",
      "Prekontroluj opodstatnenosť tohto nálezu a ako to vyriešiť.",
    ]);
    expect(REVIEW_REQUEST).toBe("Prekontroluj opodstatnenosť tohto nálezu a ako to vyriešiť.");
  });

  it("an old card without a round or a detail says nothing it does not know", () => {
    const q = reviewQuestion({ ...CARD, round: null, technicalDetail: undefined, explanation: "  " }, 20_000);
    expect(q).not.toMatch(/Kolo opráv/);
    expect(q).not.toMatch(/Technický detail/);
    expect(q.split("\n").slice(0, 3)).toEqual([
      "Rozhodnutie 1 z 1 — Verifikácia našla blokujúcu chybu. Ako chceš pokračovať?",
      "",
      "Možnosti na karte: Spustiť pripravenú opravu (odporúčané) · Usmerniť opravu (napíš vlastný pokyn) · " +
        "Zatiaľ podržať (rozhodnem neskôr)",
    ]);
  });

  it("a card as long as NEX Inbox 1.8.0's fits the limit — the technical detail gives way, the request stays", () => {
    const card = {
      ...CARD,
      explanation: "Vysvetlenie. ".repeat(440),
      technicalDetail: "- nález Auditora\n".repeat(631),
    };
    expect(reviewQuestion(card, 1_000_000).length).toBeGreaterThan(16_000); // the measured size, unshortened

    const q = reviewQuestion(card, 12_000);

    expect(q.length).toBeLessThanOrEqual(12_000);
    expect(q.length).toBeGreaterThan(11_900); // shortened to the limit, not far below it
    expect(q).toContain(card.explanation.trim()); // the explanation is whole
    expect(q).toContain("Technický detail:\n- nález Auditora\n");
    expect(q).toContain("… (skrátené — celé znenie je na karte)");
    expect(q.endsWith(`\n\n${REVIEW_REQUEST}`)).toBe(true);
  });

  it("when even the explanation does not fit, it gives way too — the heading and the request never do", () => {
    const card = { ...CARD, explanation: "Vysvetlenie. ".repeat(440), technicalDetail: "- nález\n".repeat(600) };

    const q = reviewQuestion(card, 900);

    expect(q.length).toBeLessThanOrEqual(900);
    expect(q.startsWith("Rozhodnutie 1 z 1 — Verifikácia našla blokujúcu chybu.")).toBe(true);
    expect(q.endsWith(`\n\n${REVIEW_REQUEST}`)).toBe(true);
    expect(q).toContain("Možnosti na karte: Spustiť pripravenú opravu (odporúčané)");
  });

  it("a card within the limit is never shortened", () => {
    const full = reviewQuestion(CARD, 1_000_000);
    expect(reviewQuestion(CARD, full.length)).toBe(full);
    expect(full).not.toMatch(/skrátené/);
  });

  it("shortening never leaves half of a character", () => {
    const card = { ...CARD, technicalDetail: "😀".repeat(400) };
    const full = reviewQuestion(card, 1_000_000);
    for (let limit = full.length - 60; limit < full.length; limit++) {
      const q = reviewQuestion(card, limit);
      expect(q.length).toBeLessThanOrEqual(limit);
      expect(q).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/);
    }
  });
});

describe("the hand-off between the card and Poradca", () => {
  it("is taken exactly once, and only for its own version", () => {
    handOffReview("v13", CARD);
    expect(takeReview("v99")).toBeNull();
    expect(takeReview("v13")).toEqual(CARD);
    expect(takeReview("v13")).toBeNull();
  });

  it("a second click carries the current card, not both", () => {
    handOffReview("v13", { ...CARD, round: 3 });
    handOffReview("v13", CARD);
    expect(takeReview("v13")?.round).toBe(4);
  });

  it("something unreadable in the slot is not a card", () => {
    window.localStorage.setItem("nex.poradca.review.v13", "{not json");
    expect(takeReview("v13")).toBeNull();
    window.localStorage.setItem("nex.poradca.review.v13", JSON.stringify({ question: "" }));
    expect(takeReview("v13")).toBeNull();
  });

  it("the address opens Poradca on the card's version and marks the visit as the card's", () => {
    const url = new URL(reviewAddress("v 13"), "https://kokpit");
    expect(url.pathname).toBe("/poradca");
    expect(url.searchParams.get("verzia")).toBe("v 13");
    expect(url.searchParams.has(REVIEW_PARAM)).toBe(true);
  });
});
