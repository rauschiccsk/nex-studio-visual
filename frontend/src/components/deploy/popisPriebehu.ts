import type { DeployProgress } from "@/services/api/deploy";

/** Čo napísať na tlačidlo, kým prevzatie beží (ICCINT-153).
 *
 * Bez kroku by tam zostalo holé „Preberám…" — teda presne to, čo Directora pomýlilo. S krokom aj
 * časom je vidieť, že sa pracuje, a ako dlho už. */
export function popisPriebehu(p: DeployProgress | null): string {
  if (!p || !p.bezi || !p.krok) return "Preberám…";
  const minuty = Math.floor(p.trva_sekund / 60);
  const cas = minuty >= 1 ? `${minuty} min` : `${p.trva_sekund} s`;
  return `${p.krok} · ${cas}`;
}

