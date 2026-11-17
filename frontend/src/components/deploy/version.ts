import { formatDateTime } from "@/utils/format";

/**
 * Version-number display helper shared by the deploy surface (v4.0.54).
 *
 * Until DEV-170 (21.10.2026) the graduated first-PROD version was stored "v1.0.0" while every other was "1.1.0";
 * the database now holds bare numbers only. A leading "v" is still stripped so every deploy surface reads the
 * same bare-semver form whatever it is handed (audit obs #3). The STORED value (the deploy identifier used in
 * requests / accepted_versions) is never touched — this is display-only.
 *
 * One definition: the matrix table and the block notice sit on the same screen, so a divergence here would
 * show the same version two different ways.
 */
export const fmtVer = (v: string | null | undefined): string => (v ?? "").replace(/^v/i, "");

/**
 * Kedy sa nasadilo — v ľudskej podobe, v čase čitateľa (ICCINT-117).
 *
 * Server posiela ISO 8601 v UTC; Manažér číta miestny čas v jednom tvare dátumu kokpitu (DEV-153,
 * `utils/format.ts`). Neplatný alebo chýbajúci vstup vráti prázdny reťazec — nikdy „Invalid Date“ na obrazovke.
 */
export const fmtDeployTime = (iso: string | null | undefined): string => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return formatDateTime(d);
};
