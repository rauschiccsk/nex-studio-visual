// DEV-154 — the words of the orientačná cena on the billing tab (DEV-225: its only place).
import { formatEur } from "@/utils/format";
import type { PriceEstimate } from "@/services/api/deliveryStatements";

/** „podľa 1 úlohy“, „podľa 12 úloh“. */
export const tasksWords = (n: number) => `${n} ${n === 1 ? "úlohy" : "úloh"}`;

/** „z 1 hotovej verzie“, „z 7 hotových verzií“. */
export const fromVersionsWords = (n: number) =>
  `${n} ${n === 1 ? "hotovej verzie" : "hotových verzií"}`;

/** „o 1 hotovú verziu“, „o 3 hotové verzie“, „o 5 hotových verzií“. */
export const onVersionsWords = (n: number) =>
  `${n} ${n === 1 ? "hotovú verziu" : n >= 2 && n <= 4 ? "hotové verzie" : "hotových verzií"}`;

export function amountWords(
  estimate: Pick<PriceEstimate, "amount_eur" | "spread_pct">,
): string {
  return `cca ${formatEur(estimate.amount_eur)}${estimate.spread_pct != null ? ` ± ${estimate.spread_pct} %` : ""}`;
}
