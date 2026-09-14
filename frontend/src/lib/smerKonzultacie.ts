/**
 * Smer, nie stav — čo sa nad kartami ukáže Manažérovi (ICCINT-124).
 *
 * Nad kartami stálo „rozhodni 1 zo 4 (kolo 4 z 5)". Manažér vidí STAV, nevidí SMER — nevie, či je
 * to lepšie alebo horšie než pred kolom, ani koľko sa už uzavrelo. Skutočný priebeh NEX Inboxu
 * v1.5.0, ktorý sa z kokpitu nedal prečítať:
 *
 *   kolo 1  blokujúce 5      kolo 4  blokujúce 1   ← vyzeralo ako rozpad, bol to čistý objav
 *   kolo 2  blokujúce 1      kolo 5  blokujúce 0   → prechádza
 *   kolo 3  blokujúce 0
 *
 * Blokujúcich 5 → 1 → 0 → 1 → 0, dvanásť uzavretých nálezov. Ten obraz musel Dedo poskladať ručne
 * z databázy.
 *
 * ⚠️ NEZAKRÝVAŤ ZLÉ SPRÁVY. Keď sa stavba naozaj rozpadáva — blokujúcich pribúda — musí to byť
 * z toho istého riadku vidieť rovnako zreteľne. Preto sa vypisuje surová postupnosť čísel a nie
 * hodnotenie: rad 0 → 2 → 5 hovorí sám za seba a nedá sa prečítať ako pokrok.
 */

export interface SmerKonzultacie {
  /** Koľko rozhodnutí už Manažér uzavrel (naprieč celou stavbou). */
  uzavretych: number;
  /** Koľko ešte čaká v tomto kole. */
  otvorenych: number;
  /** Koľko nálezov BLOKOVALO v každom kole, v poradí. Prázdne, keď sa ešte nič neposudzovalo. */
  blokujuce: number[];
}

interface Sprava {
  kind?: string | null;
  payload?: Record<string, unknown> | null;
  seq?: number | null;
}

function jeBlokujuci(f: unknown): boolean {
  // Nález je od ICCINT-122 údaj; staré záznamy sú holé vety a jediné, čo o nich vieme, je marker.
  if (typeof f === "string") return !f.toLowerCase().includes("neblokujúce");
  const o = (f ?? {}) as Record<string, unknown>;
  return o.blocking !== false;
}

export function smerKonzultacie(messages: Sprava[], otvorenych: number): SmerKonzultacie {
  const zoradene = [...messages].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));

  const uzavretych = zoradene.filter((m) => m.kind === "answer" && m.payload?.consultation_decision).length;

  const blokujuce = zoradene
    .filter((m) => m.kind === "verdict" && Array.isArray(m.payload?.findings))
    .map((m) => (m.payload!.findings as unknown[]).filter(jeBlokujuci).length);

  return { uzavretych, otvorenych, blokujuce };
}

/** Riadok pre človeka. `null`, keď ešte niet čo ukázať — prázdny riadok je horší než žiadny. */
export function smerDoRiadku(s: SmerKonzultacie): string | null {
  if (s.uzavretych === 0 && s.blokujuce.length === 0) return null;
  const casti = [`Uzavretých ${s.uzavretych}`, `otvorené ${s.otvorenych}`];
  if (s.blokujuce.length > 0) casti.push(`blokujúcich ${s.blokujuce.join(" → ")}`);
  return casti.join(" · ");
}
