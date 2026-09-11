/**
 * Version-number display helper shared by the deploy surface (v4.0.54).
 *
 * Some version_numbers are stored "v1.0.0" (the graduated first-PROD) and some "1.1.0" — strip a leading
 * "v" so every deploy surface reads the same bare-semver form (audit obs #3). The STORED value (the deploy
 * identifier used in requests / accepted_versions) is never touched — this is display-only.
 *
 * One definition: the matrix table and the block notice sit on the same screen, so a divergence here would
 * show the same version two different ways.
 */
export const fmtVer = (v: string | null | undefined): string => (v ?? "").replace(/^v/i, "");

/**
 * Kedy sa nasadilo — v ľudskej podobe, v čase čitateľa (ICCINT-117).
 *
 * Server posiela ISO 8601 v UTC; Manažér číta miestny čas. Formátuje sa cez `sk-SK`, nech to vyzerá
 * ako dátum a nie ako strojový zápis. Neplatný alebo chýbajúci vstup vráti prázdny reťazec — nikdy
 * „Invalid Date“ na obrazovke.
 */
export const fmtDeployTime = (iso: string | null | undefined): string => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("sk-SK", {
    day: "numeric",
    month: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};
