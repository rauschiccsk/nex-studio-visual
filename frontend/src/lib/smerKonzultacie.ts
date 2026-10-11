/**
 * Smer, nie stav — čo sa nad kartami ukáže Manažérovi (ICCINT-124, DEV-5).
 *
 * Nad kartami stálo „rozhodni 1 zo 4 (kolo 4 z 5)". Manažér vidí STAV, nevidí SMER — nevie, či je
 * to lepšie alebo horšie než pred kolom, ani koľko sa už uzavrelo. Skutočný priebeh NEX Inboxu
 * v1.5.0, ktorý sa z kokpitu nedal prečítať:
 *
 *   kolo 1  blokujúce 5      kolo 4  blokujúce 1   ← vyzeralo ako rozpad, bol to čistý objav
 *   kolo 2  blokujúce 1      kolo 5  blokujúce 0   → prechádza
 *   kolo 3  blokujúce 0
 *
 * Blokujúcich 5 → 1 → 0 → 1 → 0, dvanásť uzavretých nálezov, z toho tri také, ktoré by prešli všetkými
 * skúškami a prejavili sa až u platiaceho zákazníka. Ten obraz musel Dedo poskladať ručne z databázy.
 *
 * DEV-5: čísla počíta backend z CELÉHO záznamu verzie (`board.consultation_direction`) — chvost posledných
 * správ dlhej stavbe stratí prvé kolá — a len z kôl fázy, v ktorej karty sú. Tu sa z nich len skladá reč.
 *
 * ⚠️ NEZAKRÝVAŤ ZLÉ SPRÁVY. Keď sa stavba naozaj rozpadáva — blokujúcich pribúda — musí to byť
 * z toho istého riadku vidieť rovnako zreteľne. Preto sa vypisuje surová postupnosť čísel a nie
 * hodnotenie: rad 0 → 2 → 5 hovorí sám za seba a nedá sa prečítať ako pokrok.
 */

import type { ConsultationDirection, RoundGain } from "@/services/api/pipeline";

/** Riadok pre človeka. `null`, keď ešte niet čo ukázať — prázdny riadok je horší než žiadny. */
export function smerDoRiadku(s: ConsultationDirection | null | undefined): string | null {
  if (!s || (s.closed === 0 && s.blocking_by_round.length === 0)) return null;
  const casti = [`Uzavretých ${s.closed}`, `otvorené ${s.open}`];
  if (s.blocking_by_round.length > 0) casti.push(`blokujúcich ${s.blocking_by_round.join(" → ")}`);
  return casti.join(" · ");
}

/** Slovenský tvar podstatného mena po čísle: 1 nález, 2–4 nálezy, 0 a 5+ nálezov. */
function poCisle(n: number, jeden: string, dvaAzStyri: string, viac: string): string {
  if (n === 1) return jeden;
  if (n >= 2 && n <= 4) return dvaAzStyri;
  return viac;
}

/**
 * DEV-5 — veta o tom, čo posledné kolo previerky ZÍSKALO, nie čo pribudlo. Nález, ktorý by neodhalila žiadna
 * automatická skúška, je presne ten, ktorý by sa inak prejavil až u zákazníka — to je zisk kola. Keď nálezy kola
 * o skúškach nehovoria (staršie záznamy), veta to netvrdí. `null`, keď v tejto fáze ešte kolo nebolo.
 */
export function ziskKola(kolo: RoundGain | null | undefined): string | null {
  if (!kolo) return null;
  if (kolo.findings === 0) return "Posledné kolo previerky nenašlo nič.";
  let veta = `Posledné kolo previerky zachytilo ${kolo.findings} ${poCisle(kolo.findings, "nález", "nálezy", "nálezov")}`;
  if (kolo.blocking > 0) {
    veta += ` (z toho ${kolo.blocking} ${poCisle(kolo.blocking, "blokujúci", "blokujúce", "blokujúcich")})`;
  }
  const e = kolo.escaped ?? 0;
  if (e > 0) {
    veta +=
      e === 1
        ? "; jeden z nich by prešiel všetkými skúškami a prejavil sa až u zákazníka"
        : `; ${e} z nich by ${poCisle(e, "", "prešli", "prešlo")} všetkými skúškami a ${poCisle(
            e,
            "",
            "prejavili",
            "prejavilo",
          )} sa až u zákazníka`;
  }
  return `${veta}.`;
}
