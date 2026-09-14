/**
 * Verzia bežiacej CHRBTICE — z jej vlastnej sondy, nie z čísla vpečeného do obrazoviek (ICCINT-128).
 *
 * Pýta sa raz pri načítaní. Keď sonda nie je dostupná, vráti ``null`` a panel zostane pri tom, čo
 * vie — nikdy si nevymyslí zhodu. Nedostupná chrbtica je iná vec než zhodné verzie a nesmie sa
 * tváriť rovnako.
 */
import { useEffect, useState } from "react";

export function useBackendVersion(): string | null {
  const [verzia, setVerzia] = useState<string | null>(null);

  useEffect(() => {
    let zivy = true;
    fetch("/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (zivy && d && typeof d.version === "string") setVerzia(d.version);
      })
      .catch(() => {
        /* sonda nedostupná — panel zostane pri verzii obrazoviek, viď verziaDoPanela */
      });
    return () => {
      zivy = false;
    };
  }, []);

  return verzia;
}
