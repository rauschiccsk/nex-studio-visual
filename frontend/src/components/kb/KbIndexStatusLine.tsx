/**
 * Jeden riadok v hlavičke Dokumentácie: sedí vyhľadávanie s tým, čo je na disku? (ICCINT-111)
 *
 * Zmerané 10.09.2026: z 205 súborov Znalostnej bázy nesedelo 113 — a NIKDE to nebolo vidieť. Kto sa
 * vyhľadávania opýtal, dostal odpoveď zo sveta spred dvoch mesiacov a nijako sa nedozvedel, že ju
 * dostal. Dorovnávať rozdiel potichu by pôvodnú chybu len prelakovalo: druhá polovica opravy je, že
 * je ten rozdiel VIDNO.
 *
 * Tri stavy a ani jeden z nich nemlčí:
 *  - **sedí** — pokojný riadok, nič netreba;
 *  - **nesedí N** — koľko a ktoré (prvých pár v bublinke); dorovná sa samo do štvrťhodiny;
 *  - **nedá sa zistiť** — index nedostupný. Toto je ten dôležitý: NIE „sedí".
 */

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, HelpCircle } from "lucide-react";

import { getKbIndexStatus, type KbIndexStatus } from "@/services/api/rag";

export function KbIndexStatusLine() {
  const [stav, setStav] = useState<KbIndexStatus | null>(null);
  const [nedostupne, setNedostupne] = useState(false);

  useEffect(() => {
    let zivy = true;
    getKbIndexStatus()
      .then((s) => {
        if (zivy) setStav(s);
      })
      .catch(() => {
        if (zivy) setNedostupne(true);
      });
    return () => {
      zivy = false;
    };
  }, []);

  if (nedostupne) {
    return (
      <div
        className="mt-1 flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400"
        title="Vyhľadávanie je nedostupné, takže sa nedá zistiť, či sedí s dokumentmi na disku. Prehliadanie a čítanie funguje ďalej."
      >
        <HelpCircle size={12} />
        <span>vyhľadávanie: nedá sa zistiť</span>
      </div>
    );
  }

  if (!stav) return null;

  if (stav.out_of_sync === 0) {
    return (
      <div
        className="mt-1 flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]"
        title={`Vyhľadávanie pozná všetkých ${stav.indexed} dokumentov v aktuálnej podobe.`}
      >
        <CheckCircle2 size={12} />
        <span>vyhľadávanie sedí</span>
      </div>
    );
  }

  const ukazka = stav.sample.slice(0, 5).join("\n");
  return (
    <div
      className="mt-1 flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400"
      title={
        `Vyhľadávanie zatiaľ nepozná ${stav.out_of_sync} dokumentov v ich aktuálnej podobe ` +
        `(chýba ${stav.missing}, zastaraných ${stav.stale}, zrušených ${stav.orphaned}). ` +
        `Dorovná sa samo, zvyčajne do štvrťhodiny.` +
        (ukazka ? `\n\nNapríklad:\n${ukazka}` : "")
      }
    >
      <AlertTriangle size={12} />
      <span>vyhľadávanie nesedí: {stav.out_of_sync} dokumentov</span>
    </div>
  );
}

export default KbIndexStatusLine;
