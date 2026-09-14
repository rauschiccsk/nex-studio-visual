/**
 * Verzia do bočného panela — a čo robiť, keď sa polovice appky líšia (ICCINT-128).
 *
 * Panel dovtedy písal `VITE_APP_VERSION`, teda číslo vpečené do frontendu pri jeho zostavení, a
 * tváril sa, že je to verzia celej appky. Nie je: je to verzia jednej z jej dvoch polovíc.
 * Nasadenie samotnej chrbtice (`deploy-prod.sh backend`) frontend nepresadzuje, takže jeho číslo
 * zostane staré — 14.09.2026 o štyri vydania.
 *
 * ⚠️ Riešením NIE JE nasadzovať vždy obe polovice. Nasadenie samotnej chrbtice má zmysel a je
 * zámerne odporúčané: pri zmenách bez obrazoviek nechá bežiacu stavbu na pokoji a Manažérovi
 * nepreblikne rozpracovaná stránka. Chyba nie je v tom, že sa polovice líšia — chyba je, že sa to
 * nedá zbadať.
 *
 * Vedie číslo CHRBTICE, lebo to je tá polovica, ktorá rozhoduje o správaní. Manažér pri hlásení
 * problému uvedie číslo z panela; kto to bude vyšetrovať, musí dostať vydanie, ktoré naozaj beží.
 */
export function verziaDoPanela(backend: string | null | undefined, frontend: string | null | undefined): string {
  const fe = (frontend || "").trim();
  const be = (backend || "").trim();

  // Chrbtica sa ešte neozvala (prvé vykreslenie, nedostupná sonda). Nevymýšľame si zhodu —
  // ukážeme to, čo naozaj vieme, a druhá polovica pribudne, keď odpoveď príde.
  if (!be) return `v${fe || "dev"}`;

  if (!fe) return `v${be} · obrazovky neznáme`;
  if (fe === be) return `v${be}`;
  return `v${be} · obrazovky v${fe}`;
}
