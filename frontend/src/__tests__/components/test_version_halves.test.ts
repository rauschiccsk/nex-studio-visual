/**
 * ICCINT-128 — panel nesmie vydávať verziu obrazoviek za verziu celej appky.
 *
 * Zbadal Director 14.09.2026 na vlastnej obrazovke: panel hlásil v4.38.0, kým bežala v4.38.4.
 * Jeho slová: „Nesedí mi verzia."
 *
 *   nex-studio-visual-prod-backend-1    nex-studio-visual-backend:v4.38.4
 *   nex-studio-visual-prod-frontend-1   nex-studio-visual-frontend:v4.38.0   ← štyri vydania pozadu
 *   panel:                              v4.38.0
 *
 * Príčina je priama: panel číta `VITE_APP_VERSION`, teda číslo vpečené do frontendu pri jeho
 * zostavení. Nasadenie samotnej chrbtice (legitímna a odporúčaná cesta pri zmenách bez obrazoviek)
 * frontend nepresadzuje, takže jeho číslo zostane staré.
 *
 * ⚠️ Riešením NIE JE nasadzovať vždy obe polovice. Nasadenie samotnej chrbtice má zmysel — pri
 * zmenách bez obrazoviek nechá bežiacu stavbu na pokoji a Manažérovi nepreblikne rozpracovaná
 * stránka. Chyba nie je v tom, že sa polovice líšia. Chyba je, že sa to nedá zbadať.
 */

import { describe, expect, it } from "vitest";

import { verziaDoPanela } from "@/lib/verziaDoPanela";

describe("Verzia v paneli (ICCINT-128)", () => {
  it("pri zhode ukáže jedno číslo, ako doteraz", () => {
    expect(verziaDoPanela("4.38.4", "4.38.4")).toBe("v4.38.4");
  });

  it("⚠️ pri rozdiele ukáže OBE — inak panel hovorí o celku, kým hovorí o časti", () => {
    expect(verziaDoPanela("4.38.4", "4.38.0")).toBe("v4.38.4 · obrazovky v4.38.0");
  });

  it("kým sa chrbtica nespýtala, ukáže to, čo vie — nevymýšľa si zhodu", () => {
    expect(verziaDoPanela(null, "4.38.0")).toBe("v4.38.0");
  });

  it("keď nevieme ani jedno, ostáva „vdev“ ako doteraz — správanie sa nemení tam, kde bolo správne", () => {
    expect(verziaDoPanela(null, undefined)).toBe("vdev");
  });

  it("neznáme číslo obrazoviek sa nevydáva za verziu", () => {
    expect(verziaDoPanela("4.38.4", undefined)).toBe("v4.38.4 · obrazovky neznáme");
  });

  it("vedie číslo CHRBTICE — to je tá polovica, ktorá rozhoduje o správaní", () => {
    // Manažér pri hlásení problému uvedie číslo z panela. Kto to bude vyšetrovať, musí dostať
    // vydanie, ktoré naozaj beží — nie to, z ktorého sú obrazovky.
    expect(verziaDoPanela("4.39.0", "4.38.0").startsWith("v4.39.0")).toBe(true);
  });
});
