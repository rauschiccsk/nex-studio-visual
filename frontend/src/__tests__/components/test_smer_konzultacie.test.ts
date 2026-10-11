/**
 * ICCINT-124, DEV-5 — nad kartami SMER, nie stav, a veta o tom, čo kolo ZÍSKALO.
 *
 * Manažér nemá vidieť, koľko zostáva. Má vidieť, KAM TO IDE. Čísla počíta backend z celého záznamu verzie
 * (stráže počítania: tests/test_the_line_over_the_cards_shows_direction_and_gain.py); tu sa z nich skladá reč.
 */

import { describe, expect, it } from "vitest";

import { smerDoRiadku, ziskKola } from "@/lib/smerKonzultacie";
import type { ConsultationDirection } from "@/services/api/pipeline";

const smer = (closed: number, open: number, blocking_by_round: number[]): ConsultationDirection => ({
  closed,
  open,
  blocking_by_round,
  last_round: null,
});

describe("Smer nad kartami (ICCINT-124)", () => {
  it("poskladá presne ten priebeh, ktorý sa z kokpitu nedal prečítať", () => {
    // NEX Inbox v1.5.0, Návrh, 11.–12.09.2026 — dvanásť uzavretých, blokujúcich 5 → 1 → 0 → 1 → 0.
    expect(smerDoRiadku(smer(12, 4, [5, 1, 0, 1, 0]))).toBe("Uzavretých 12 · otvorené 4 · blokujúcich 5 → 1 → 0 → 1 → 0");
  });

  it("⚠️ rozpad je vidieť rovnako zreteľne ako pokrok — riadok nehodnotí, vypisuje", () => {
    expect(smerDoRiadku(smer(0, 3, [0, 2, 5]))).toBe("Uzavretých 0 · otvorené 3 · blokujúcich 0 → 2 → 5");
  });

  it("kým niet čo ukázať, riadok sa nevykreslí — prázdny riadok je horší než žiadny", () => {
    expect(smerDoRiadku(smer(0, 2, []))).toBeNull();
    expect(smerDoRiadku(null)).toBeNull();
    expect(smerDoRiadku(undefined)).toBeNull();
  });

  it("uzavreté bez previerky: riadok bez blokujúcich", () => {
    expect(smerDoRiadku(smer(3, 1, []))).toBe("Uzavretých 3 · otvorené 1");
  });
});

describe("Čo posledné kolo získalo (DEV-5)", () => {
  it.each([
    [
      { findings: 1, blocking: 1, escaped: 1 },
      "Posledné kolo previerky zachytilo 1 nález (z toho 1 blokujúci); jeden z nich by prešiel všetkými skúškami a prejavil sa až u zákazníka.",
    ],
    [
      { findings: 3, blocking: 2, escaped: 2 },
      "Posledné kolo previerky zachytilo 3 nálezy (z toho 2 blokujúce); 2 z nich by prešli všetkými skúškami a prejavili sa až u zákazníka.",
    ],
    [
      { findings: 6, blocking: 5, escaped: 5 },
      "Posledné kolo previerky zachytilo 6 nálezov (z toho 5 blokujúcich); 5 z nich by prešlo všetkými skúškami a prejavilo sa až u zákazníka.",
    ],
    [{ findings: 2, blocking: 0, escaped: 0 }, "Posledné kolo previerky zachytilo 2 nálezy."],
    [{ findings: 4, blocking: 1, escaped: null }, "Posledné kolo previerky zachytilo 4 nálezy (z toho 1 blokujúci)."],
    [{ findings: 0, blocking: 0, escaped: null }, "Posledné kolo previerky nenašlo nič."],
  ])("%o → povie, čo sa získalo, po slovensky správne", (kolo, veta) => {
    expect(ziskKola(kolo)).toBe(veta);
  });

  it("keď kolo o skúškach nehovorí, veta netvrdí nič o zákazníkovi", () => {
    expect(ziskKola({ findings: 3, blocking: 3, escaped: null })).not.toMatch(/zákazník/);
  });

  it("kým v tejto fáze kolo nebolo, veta nie je", () => {
    expect(ziskKola(null)).toBeNull();
    expect(ziskKola(undefined)).toBeNull();
  });
});
