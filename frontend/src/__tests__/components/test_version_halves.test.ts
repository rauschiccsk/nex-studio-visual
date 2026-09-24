/**
 * ICCINT-128 — panel nesmie vydávať verziu frontendu za verziu celej aplikácie.
 *
 * Zbadal Director 14.09.2026 na vlastnej obrazovke: panel hlásil v4.38.0, kým bežala v4.38.4.
 * Jeho slová: „Nesedí mi verzia."
 *
 *   nex-studio-visual-prod-backend-1    nex-studio-visual-backend:v4.38.4
 *   nex-studio-visual-prod-frontend-1   nex-studio-visual-frontend:v4.38.0   ← štyri vydania pozadu
 *   panel:                              v4.38.0
 *
 * Príčina je priama: panel čítal `VITE_APP_VERSION`, teda číslo vpečené do frontendu pri jeho
 * zostavení. Nasadenie samotného backendu (legitímna a odporúčaná cesta pri zmenách bez obrazoviek)
 * frontend nepresadzuje, takže jeho číslo zostane staré.
 *
 * ⚠️ **A 24.09.2026 sa to stalo ZNOVU — hoci oprava už bola v kóde.** Panel hlásil v4.40.2, kým
 * backend bežal na v4.40.7. Číslo backendu totiž do prehliadača nikdy nedorazilo: panel sa naň pýtal
 * na `/health`, a tú adresu si odchytáva nginx frontendu a odpovedá zaň sám, bez verzie. Pravidlo
 * nižšie fungovalo správne — dostávalo `null` a poctivo spadlo späť na číslo frontendu.
 * Preto je tu aj stráž nad ADRESOU: pravidlo bez dosiahnuteľného čísla je poistka, ktorá nedrží.
 *
 * ⚠️ Riešením NIE JE nasadzovať vždy obe polovice. Nasadenie samotného backendu má zmysel — pri
 * zmenách bez obrazoviek nechá bežiacu stavbu na pokoji a Manažérovi nepreblikne rozpracovaná
 * stránka. Chyba nie je v tom, že sa polovice líšia. Chyba je, že sa to nedá zbadať.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { CESTA_NA_VERZIU } from "@/hooks/useBackendVersion";
import { verziaDoPanela } from "@/lib/verziaDoPanela";

describe("Verzia v paneli (ICCINT-128)", () => {
  it("pri zhode ukáže jedno číslo, ako doteraz", () => {
    expect(verziaDoPanela("4.38.4", "4.38.4")).toBe("v4.38.4");
  });

  it("⚠️ pri rozdiele ukáže OBE a MENOM — inak panel hovorí o celku, kým hovorí o časti", () => {
    expect(verziaDoPanela("4.38.4", "4.38.0")).toBe("backend v4.38.4 · frontend v4.38.0");
  });

  it("kým sa backend neozval, ukáže to, čo vie — nevymýšľa si zhodu", () => {
    expect(verziaDoPanela(null, "4.38.0")).toBe("v4.38.0");
  });

  it("keď nevieme ani jedno, ostáva „vdev“ ako doteraz — správanie sa nemení tam, kde bolo správne", () => {
    expect(verziaDoPanela(null, undefined)).toBe("vdev");
  });

  it("neznáme číslo frontendu sa nevydáva za verziu", () => {
    expect(verziaDoPanela("4.38.4", undefined)).toBe("backend v4.38.4 · frontend neznámy");
  });

  it("obe čísla sú v texte pomenované — z holého čísla sa nedá zistiť, čie je", () => {
    const text = verziaDoPanela("4.39.0", "4.38.0");
    expect(text).toContain("backend v4.39.0");
    expect(text).toContain("frontend v4.38.0");
  });
});

/**
 * Stráž nad ADRESOU. Pravidlo vyššie bolo celý čas správne a aj tak panel klamal — lebo číslo
 * backendu sa k nemu nemalo ako dostať. Tieto stráže sa pýtajú nastavenia samotného nginxu, nie
 * mojej predstavy o ňom.
 */
describe("Cesta, ktorou sa panel pýta na verziu (ICCINT-128)", () => {
  const nginx = readFileSync(resolve(__dirname, "../../../nginx.conf"), "utf-8");

  it("⚠️ nesmie to byť adresa, ktorú si nginx frontendu odchytáva a odpovedá zaň sám", () => {
    // `location = /health` vracia {"service":"frontend"} — bez verzie. Presne na tom to padlo.
    const odchytene = [...nginx.matchAll(/location\s*=\s*(\S+)\s*\{([^}]*)\}/g)]
      .filter(([, , telo]) => /return\s+\d{3}/.test(telo ?? ""))
      .map(([, cesta]) => cesta)
      .filter((c): c is string => Boolean(c));

    expect(odchytene).not.toContain(CESTA_NA_VERZIU);
  });

  it("musí ležať pod predponou, ktorú nginx posiela na backend", () => {
    const presmerovane = [...nginx.matchAll(/location\s+(\S+)\s*\{[^}]*proxy_pass[^}]*\}/g)]
      .map(([, cesta]) => cesta)
      .filter((c): c is string => Boolean(c));

    expect(presmerovane.some((p) => CESTA_NA_VERZIU.startsWith(p))).toBe(true);
  });

  it("tá istá predpona musí byť presmerovaná aj vo vývoji — inak to tam ticho nefunguje", () => {
    const vite = readFileSync(resolve(__dirname, "../../../vite.config.ts"), "utf-8");
    const presmerovane = [...vite.matchAll(/"(\/[^"]*)":\s*\{/g)]
      .map(([, cesta]) => cesta)
      .filter((c): c is string => Boolean(c));

    expect(presmerovane.some((p) => CESTA_NA_VERZIU.startsWith(p))).toBe(true);
  });
});
