/**
 * ICCINT-124 — nad kartami SMER, nie stav.
 *
 * Manažér nemá vidieť, koľko zostáva. Má vidieť, KAM TO IDE.
 */

import { describe, expect, it } from "vitest";

import { smerDoRiadku, smerKonzultacie } from "@/lib/smerKonzultacie";

const odpoved = (seq: number) => ({ kind: "answer", seq, payload: { consultation_decision: { key: `k${seq}` } } });
const verdikt = (seq: number, blok: number, mensie = 0) => ({
  kind: "verdict",
  seq,
  payload: {
    findings: [
      ...Array.from({ length: blok }, (_, i) => ({ text: `blokujúce ${i}`, blocking: true })),
      ...Array.from({ length: mensie }, (_, i) => ({ text: `menšie ${i}`, blocking: false })),
    ],
  },
});

describe("Smer nad kartami (ICCINT-124)", () => {
  it("poskladá presne ten priebeh, ktorý sa z kokpitu nedal prečítať", () => {
    // NEX Inbox v1.5.0, Návrh, 11.–12.09.2026 — dvanásť uzavretých, blokujúcich 5 → 1 → 0 → 1 → 0.
    const msgs = [
      verdikt(1, 5, 1),
      ...Array.from({ length: 6 }, (_, i) => odpoved(10 + i)),
      verdikt(20, 1, 4),
      ...Array.from({ length: 2 }, (_, i) => odpoved(30 + i)),
      verdikt(40, 0, 1),
      verdikt(50, 1, 1),
      ...Array.from({ length: 4 }, (_, i) => odpoved(60 + i)),
      verdikt(70, 0, 1),
    ];

    const s = smerKonzultacie(msgs, 0);

    expect(s.blokujuce).toEqual([5, 1, 0, 1, 0]);
    expect(s.uzavretych).toBe(12);
    expect(smerDoRiadku(s)).toBe("Uzavretých 12 · otvorené 0 · blokujúcich 5 → 1 → 0 → 1 → 0");
  });

  it("⚠️ rozpad je vidieť rovnako zreteľne ako pokrok — riadok nehodnotí, vypisuje", () => {
    const s = smerKonzultacie([verdikt(1, 0), verdikt(2, 2), verdikt(3, 5)], 3);
    expect(smerDoRiadku(s)).toContain("blokujúcich 0 → 2 → 5");
  });

  it("neblokujúce nálezy sa do blokujúcich nepočítajú", () => {
    expect(smerKonzultacie([verdikt(1, 1, 9)], 0).blokujuce).toEqual([1]);
  });

  it("starý nález ako holá veta sa ďalej započíta podľa svojho markera", () => {
    const stary = { kind: "verdict", seq: 1, payload: { findings: ["Export nevracia DIČ", "Preklep (neblokujúce)"] } };
    expect(smerKonzultacie([stary], 0).blokujuce).toEqual([1]);
  });

  it("kým niet čo ukázať, riadok sa nevykreslí — prázdny riadok je horší než žiadny", () => {
    expect(smerDoRiadku(smerKonzultacie([], 0))).toBeNull();
  });

  it("poradie sa berie zo seq, nie z poradia v poli", () => {
    const s = smerKonzultacie([verdikt(30, 0), verdikt(10, 5), verdikt(20, 1)], 0);
    expect(s.blokujuce).toEqual([5, 1, 0]);
  });
});
