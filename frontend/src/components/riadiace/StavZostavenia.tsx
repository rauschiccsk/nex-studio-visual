// StavZostavenia — jedna veta o poslednom zostavení, vedľa fáz (ICCINT-129, druhá polovica).
//
// Brána Verifikácie si kedysi pýtala od GitHubu JEDEN beh. Jeden commit však spúšťa viac pracovných
// postupov, takže brána dostala ten, ktorý sa zaregistroval posledný — hod mincou. 14.09.2026 prešiel
// NEX Inbox 1.5.0 do stavu Hotovo s padajúcim zostavením; jedna z padajúcich skúšok strážila štítok,
// ktorý pri úpravách vzhľadu ticho vypadol z obrazovky. Tá polovica je opravená.
//
// TOTO je druhá polovica: Manažér sa o červenom CI dozvedel až NA BRÁNE — čiže vtedy, keď je verzia
// „hotová" a prerába sa. Kto vidí červenú pri druhom commite, sa do toho stavu nedostane.
//
// ⚠️ Nevedomosť nie je dobrá správa. `unknown` má vlastnú, neutrálnu podobu — nikdy sa netvári ako
// zelená. A veta pri červenej menuje POSTUP aj číslo behu: 14.09. brána ohlásila „CI zelené
// (beh 34869175048)" o behu úplne iného postupu, takže jediné slovo, na ktoré sa Manažér spoliehal,
// bolo to nesprávne.

import { useEffect, useState } from "react";

import { getCiStatusApi, type CiStatus } from "@/services/api/pipeline";

/** Ako často sa pýtať. Odpoveď si server pamätá minútu, takže častejšie by nemalo čo priniesť. */
const OBNOVA_MS = 60_000;

interface Podoba {
  znak: string;
  trieda: string;
  nadpis: string;
}

/** Keď stav nepoznáme — a rovnako aj keď nám server pošle slovo, ktoré nepoznáme. Nikdy nie zelená. */
const NEVIEME: Podoba = {
  znak: "○",
  trieda: "text-[var(--color-text-muted)]",
  nadpis: "Zostavenie: zatiaľ nevieme",
};

const PODOBA: Record<string, Podoba> = {
  green: {
    znak: "●",
    trieda: "text-emerald-600 dark:text-emerald-400",
    nadpis: "Zostavenie prešlo",
  },
  red: {
    znak: "●",
    trieda: "text-red-600 dark:text-red-400",
    nadpis: "Zostavenie zlyhalo",
  },
  unknown: NEVIEME,
};

interface Props {
  versionId: string | null;
}

export default function StavZostavenia({ versionId }: Props) {
  const [stav, setStav] = useState<CiStatus | null>(null);

  useEffect(() => {
    if (!versionId) {
      setStav(null);
      return;
    }
    let zrusene = false;
    // Zlyhanie tohto dopytu nesmie nič zhodiť ani nič tvrdiť: keď sa nedozvieme, nezobrazíme nič.
    // Riadok, ktorý po výpadku siete ukáže starú zelenú, je horší než riadok, ktorý nie je.
    // ⚠️ `Promise.resolve().then(...)` a nie holé volanie: keby `getCiStatusApi` chýbalo (napríklad
    // v atrape modulu), volanie vyhodí chybu SYNCHRÓNNE — a tá by neskončila v `.catch`, ale zhodila
    // by celú obrazovku. Presne to sa 25.09.2026 stalo stránke projektu pri inom novom dopyte.
    const natiahni = () =>
      Promise.resolve()
        .then(() => getCiStatusApi(versionId))
        .then((v) => {
          if (!zrusene) setStav(v);
        })
        .catch(() => {
          if (!zrusene) setStav(null);
        });
    natiahni();
    const timer = setInterval(natiahni, OBNOVA_MS);
    return () => {
      zrusene = true;
      clearInterval(timer);
    };
  }, [versionId]);

  if (!stav) return null;

  const podoba = PODOBA[stav.stav] ?? NEVIEME;

  return (
    <div
      className="flex items-center gap-2 border-t border-[var(--color-border-default)] px-4 py-1.5 text-xs"
      data-testid="stav-zostavenia"
    >
      <span className={podoba.trieda} aria-hidden="true">
        {podoba.znak}
      </span>
      <span className="font-medium text-[var(--color-text-primary)]">{podoba.nadpis}</span>
      <span className="min-w-0 truncate text-[var(--color-text-muted)]" title={stav.detail}>
        {stav.detail}
      </span>
    </div>
  );
}
