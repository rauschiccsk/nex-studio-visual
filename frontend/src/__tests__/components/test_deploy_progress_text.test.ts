/**
 * ICCINT-153 — na tlačidle musí byť vidieť, že sa pracuje, a ako dlho.
 *
 * Director 25.09.2026 pri prevzatí ostrého NEX Inboxu: „už niekoľko minút vidím tú istú obrazovku
 * bez zmeny, bez informácie, že niečo sa deje. To treba napraviť, lebo mýli ma, neviem či skutočne
 * niečo sa robí, alebo zamrzol systém."
 *
 * Medzitým sa na server zákazníka prenášal zdrojový kód, stavali sa tri obrazy a reštartovali
 * kontajnery ostrej prevádzky — a na obrazovke bolo jediné slovo „Preberám…".
 */

import { describe, expect, it } from "vitest";

import { popisPriebehu } from "@/components/deploy/popisPriebehu";

describe("Popis priebehu na tlačidle (ICCINT-153)", () => {
  it("⚠️ ukáže KROK, nie len že sa niečo deje", () => {
    const text = popisPriebehu({ bezi: true, krok: "stavím obrazy", trva_sekund: 12, krok_trva_sekund: 12 });
    expect(text).toContain("stavím obrazy");
  });

  it("⚠️ a ukáže, ako dlho to už beží — to je odpoveď na „zamrzlo to?“", () => {
    const text = popisPriebehu({ bezi: true, krok: "stavím obrazy", trva_sekund: 300, krok_trva_sekund: 60 });
    expect(text).toContain("5 min");
  });

  it("pod minútu hlási sekundy — inak by prvé pol minúty ukazovalo „0 min“", () => {
    const text = popisPriebehu({ bezi: true, krok: "spúšťam kontajnery", trva_sekund: 12, krok_trva_sekund: 12 });
    expect(text).toContain("12 s");
    expect(text).not.toContain("0 min");
  });

  it("kým odpoveď o priebehu nedorazila, ostáva pôvodné slovo — nevymýšľa sa krok", () => {
    expect(popisPriebehu(null)).toBe("Preberám…");
  });

  it("keď nasadenie nebeží, tiež sa nič nevymýšľa", () => {
    expect(popisPriebehu({ bezi: false, krok: null, trva_sekund: 0, krok_trva_sekund: 0 })).toBe("Preberám…");
  });
});
