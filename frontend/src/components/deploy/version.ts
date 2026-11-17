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
 * DEV-416 — is version `a` older than `b`? Compared as numbers, a leading "v" ignored, a part that is not a number
 * counts as 0 — the one order of versions in the cockpit (backend `services/version.py` `number_order_key`).
 */
export const isOlderVersion = (a: string, b: string): boolean => {
  const key = (v: string) => fmtVer(v).split(".").map((part) => (/^\d+$/.test(part) ? Number(part) : 0));
  const [x, y] = [key(a), key(b)];
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] ?? 0) - (y[i] ?? 0);
    if (d !== 0) return d < 0;
  }
  return false;
};

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
