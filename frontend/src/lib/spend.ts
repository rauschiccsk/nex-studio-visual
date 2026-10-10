// DEV-49 — the line under each EPIC, FEAT and TASK of the plan: „Trvalo 11 min 24 s · cena 4,05 € · 12,3 mil.
// tokenov“, the way Poradca ends its answers (Director 10.10.2026).

import { formatDuration } from "@/lib/poradcaAnswer";
import type { NodeSpend } from "@/types/task-plan";

const nf = (digits: number) =>
  new Intl.NumberFormat("sk-SK", { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** „1 token“, „3 tokeny“, „850 tokenov“, „12,3 tis. tokenov“, „3,1 mil. tokenov“. */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${nf(1).format(n / 1_000_000)} mil. tokenov`;
  if (n >= 1_000) return `${nf(1).format(n / 1_000)} tis. tokenov`;
  if (n === 1) return "1 token";
  if (n >= 2 && n <= 4) return `${n} tokeny`;
  return `${n} tokenov`;
}

function euros(eur: number): string {
  return `${nf(2).format(eur)} €`;
}

/** The price part — „aspoň“ when some spend could not be priced: never a partial figure posing as the whole. */
export function spendPrice(spend: NodeSpend): string {
  if (spend.eur == null) return "cena sa nedá vyčísliť";
  return spend.eur_complete ? `cena ${euros(spend.eur)}` : `cena aspoň ${euros(spend.eur)}`;
}

export function spendLine(spend: NodeSpend): string {
  return `Trvalo ${formatDuration(spend.seconds)} · ${spendPrice(spend)} · ${formatTokens(spend.tokens.total)}`;
}

/** What the tokens are made of (as in Náklady) and why a price is incomplete — the hover text. */
export function spendDetail(spend: NodeSpend): string {
  const t = spend.tokens;
  const int = nf(0);
  const lines = [
    `Vstup ${int.format(t.input)} · výstup ${int.format(t.output)} · čítanie z vyrovnávacej pamäte ` +
      `${int.format(t.cache_read)} · zápis do nej ${int.format(t.cache_write)} tokenov`,
    `Čas = práca agenta (${spend.turns} ${spend.turns === 1 ? "ťah" : spend.turns <= 4 ? "ťahy" : "ťahov"}), ` +
      "bez čakania na Manažéra",
  ];
  if (spend.unpriced.length) lines.push(`Cena je neúplná: ${spend.unpriced.join("; ")}`);
  return lines.join("\n");
}
