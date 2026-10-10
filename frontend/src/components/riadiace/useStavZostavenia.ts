// useStavZostavenia — stav zostavenia verzie pre celú stránku Riadiaceho centra (ICCINT-129, DEV-51).
//
// DEV-51: jedno načítanie, rozdané trom miestam — riadku pri fázach (StavZostavenia), pruhu stavu
// (HonestStatusStrip) aj pravému panelu (PlanUlohRail). 10.10.2026 pri NEX Inboxe 1.7.0 stálo nad sebou
// „Zostavenie zlyhalo“ a „Hotovo — pripravené na nasadenie“; z jedného zdroja si protirečiť nemôžu.

import { useEffect, useState } from "react";

import { getCiStatusApi, type CiStatus } from "@/services/api/pipeline";

/** Ako často sa pýtať. Odpoveď si server pamätá minútu, takže častejšie by nemalo čo priniesť. */
const OBNOVA_MS = 60_000;

/**
 * Stav zostavenia verzie, obnovovaný každú minútu (DEV-51: jedno načítanie pre celú stránku — riadok pri fázach,
 * pruh stavu aj pravý panel z neho čítajú to isté, takže si nemôžu protirečiť).
 */
export function useStavZostavenia(versionId: string | null): CiStatus | null {
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

  return stav;
}

