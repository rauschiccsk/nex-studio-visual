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
  CHOICE_REQUEST,
  REVIEW_PARAM,
  REVIEW_REQUEST,
  handOffReview,
  reviewAddress,
  reviewQuestion,
  reviewRequestFor,
  takeReview,
  type ReviewCard,
} from "@/lib/poradcaReview";

const CARD: ReviewCard = {
  position: 1,
  total: 1,
  round: 4,
  roundMax: null,
  request: REVIEW_REQUEST,
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

describe("DEV-106 — every card, its own request", () => {
  it("a card the agent prepared asks which option to choose, with its round, origin and rationale", () => {
    const card: ReviewCard = {
      position: 2,
      total: 3,
      round: 2,
      roundMax: 5,
      request: reviewRequestFor("vizual_conflict"),
      question: "Faktúra bez IČO: zobraziť ju v zozname, alebo ju skryť?",
      origin: "Vyplýva z tvojho rozhodnutia „Zoznam faktúr“ (tvoja voľba: Všetky).",
      explanation: "Vizuál ju ukazuje, Špecifikácia ju vylučuje.",
      options: [{ label: "Zobraziť", recommended: true }, { label: "Skryť" }],
      rationale: "Zobraziť — faktúra bez IČO je stále faktúra.",
    };

    expect(reviewQuestion(card, 20_000).split("\n")).toEqual([
      "Rozhodnutie 2 z 3 — Faktúra bez IČO: zobraziť ju v zozname, alebo ju skryť?",
      "Kolo konzultácie 2 z 5.",
      "Vyplýva z tvojho rozhodnutia „Zoznam faktúr“ (tvoja voľba: Všetky).",
      "",
      "Vizuál ju ukazuje, Špecifikácia ju vylučuje.",
      "",
      "Možnosti na karte: Zobraziť (odporúčané) · Skryť",
      "",
      "Zdôvodnenie na karte: Zobraziť — faktúra bez IČO je stále faktúra.",
      "",
      "Poraď mi, ktorú možnosť zvoliť a prečo.",
    ]);
  });

  it("the Auditor's findings ask to check the finding; everything else asks which option to choose", () => {
    expect(CHOICE_REQUEST).toBe("Poraď mi, ktorú možnosť zvoliť a prečo.");
    for (const source of ["verifikacia_fix", "verifikacia_fail", "auditor_upfront"]) {
      expect(reviewRequestFor(source)).toBe(REVIEW_REQUEST);
    }
    for (const source of ["vizual_conflict", "agent_ambiguity", "build_blocker", "niečo nové", undefined]) {
      expect(reviewRequestFor(source)).toBe(CHOICE_REQUEST);
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
    expect(takeReview("v13")).toEqual(CARD);
  });

  it("something unreadable in the slot is not a card", () => {
    window.localStorage.setItem("nex.poradca.review.v13", "{not json");
    expect(takeReview("v13")).toBeNull();
    window.localStorage.setItem("nex.poradca.review.v13", JSON.stringify({ question: "" }));
    expect(takeReview("v13")).toBeNull();
    window.localStorage.setItem("nex.poradca.review.v13", JSON.stringify({ ...CARD, request: undefined }));
    expect(takeReview("v13")).toBeNull();
  });

  it("the address opens Poradca on the card's version and marks the visit as the card's", () => {
    const url = new URL(reviewAddress("v 13"), "https://kokpit");
    expect(url.pathname).toBe("/poradca");
    expect(url.searchParams.get("verzia")).toBe("v 13");
    expect(url.searchParams.has(REVIEW_PARAM)).toBe(true);
  });
});
