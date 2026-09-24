/**
 * Verzia do bočného panela — a čo robiť, keď sa polovice aplikácie líšia (ICCINT-128).
 *
 * Panel dovtedy písal `VITE_APP_VERSION`, teda číslo vpečené do frontendu pri jeho zostavení, a
 * tváril sa, že je to verzia celej aplikácie. Nie je: je to verzia jednej z jej dvoch polovíc.
 * Nasadenie samotného backendu (`deploy-prod.sh backend`) frontend nepresadzuje, takže jeho číslo
 * zostane staré — 14.09.2026 o štyri vydania.
 *
 * ⚠️ Riešením NIE JE nasadzovať vždy obe polovice. Nasadenie samotného backendu má zmysel a je
 * zámerne odporúčané: pri zmenách bez obrazoviek nechá bežiacu stavbu na pokoji a Manažérovi
 * nepreblikne rozpracovaná stránka. Chyba nie je v tom, že sa polovice líšia — chyba je, že sa to
 * nedá zbadať.
 *
 * **Obe čísla sa píšu MENOM** (Director 24.09.2026). Dovtedy tu stálo `v4.40.7 · obrazovky v4.40.2`:
 * viedlo holé číslo, o ktorom sa až z druhej polovice vety dalo dovtípiť, čie je. Pri rozdiele teda
 * `backend v4.40.7 · frontend v4.40.2`. Pri zhode zostáva jediné číslo — dve rovnaké vedľa seba by
 * pri každom bežnom nasadení len zaberali miesto a čítali by sa ako porucha.
 */
export function verziaDoPanela(backend: string | null | undefined, frontend: string | null | undefined): string {
  const fe = (frontend || "").trim();
  const be = (backend || "").trim();

  // Backend sa ešte neozval (prvé vykreslenie, nedostupná cesta). Nevymýšľame si zhodu —
  // ukážeme to, čo naozaj vieme, a druhá polovica pribudne, keď odpoveď príde.
  if (!be) return `v${fe || "dev"}`;

  if (!fe) return `backend v${be} · frontend neznámy`;
  if (fe === be) return `v${be}`;
  return `backend v${be} · frontend v${fe}`;
}
