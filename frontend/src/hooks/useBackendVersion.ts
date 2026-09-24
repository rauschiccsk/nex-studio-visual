/**
 * Verzia bežiaceho BACKENDU — z jeho vlastnej cesty, nie z čísla vpečeného do frontendu (ICCINT-128).
 *
 * ⚠️ Pýta sa na `/api/v1/version`, a tá adresa nie je ľubovoľná. Pôvodne tu stálo `/health` a číslo
 * odtiaľ NIKDY neprišlo: `/health` si odchytáva nginx frontendu a odpovedá zaň sám, bez verzie.
 * Panel preto spadol späť na číslo frontendu a vydával ho za verziu celej aplikácie — presne tá
 * chyba, ktorú mal tento tiket odstrániť. Zbadal to Director 24.09.2026 (panel v4.40.2, backend
 * v4.40.7). Predpona `/api/` je presmerovaná na backend v prevádzke aj vo vývoji; čokoľvek mimo nej
 * by vo vývoji ticho nefungovalo.
 *
 * Pýta sa raz pri načítaní. Keď cesta nie je dostupná, vráti ``null`` a panel zostane pri tom, čo
 * vie — nikdy si nevymyslí zhodu. Nedostupný backend je iná vec než zhodné verzie a nesmie sa
 * tváriť rovnako.
 */
import { useEffect, useState } from "react";

/** Adresa, na ktorej backend vydáva svoju verziu. Jedno miesto — stráž sa pýta na to isté. */
export const CESTA_NA_VERZIU = "/api/v1/version";

export function useBackendVersion(): string | null {
  const [verzia, setVerzia] = useState<string | null>(null);

  useEffect(() => {
    let zivy = true;
    fetch(CESTA_NA_VERZIU)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (zivy && d && typeof d.version === "string") setVerzia(d.version);
      })
      .catch(() => {
        /* cesta nedostupná — panel zostane pri verzii frontendu, viď verziaDoPanela */
      });
    return () => {
      zivy = false;
    };
  }, []);

  return verzia;
}
