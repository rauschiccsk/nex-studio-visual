// How a Poradca answer is shown to a person (ICCINT-167). Pure functions — tests in `test_poradcaAnswer`.

import type { PoradcaVersionInfo } from "@/types/poradca";

/** Blocks from the Poradca charter (part 4) and the heading under which a person sees them. Cockpit makes buttons. */
const BLOCKS: { tag: string; heading: string }[] = [
  { tag: "pokyn-pre-agenta", heading: "Pokyn pre agenta stavby" },
  { tag: "poziadavka-do-zasobnika", heading: "Požiadavka do Zásobníka" },
  // DEV-29: answers from before the change carry the old block — it, too, only ever goes to the Zásobník.
  { tag: "poziadavka-na-novu-verziu", heading: "Požiadavka do Zásobníka" },
];

/**
 * Answer text to display: blocks with tags turn into a quote with a heading. Without that the Markdown renderer
 * (it does not let HTML through) would discard them with the content and a person would not see what a button inserts.
 */
export function answerForDisplay(content: string): string {
  let out = content;
  for (const { tag, heading } of BLOCKS) {
    const re = new RegExp(`<${tag}>\\s*([\\s\\S]*?)\\s*</${tag}>`, "g");
    out = out.replace(re, (_m, body: string) => {
      const quoted = body
        .trim()
        .split("\n")
        .map((line) => `> ${line}`)
        .join("\n");
      return `\n**${heading}:**\n\n${quoted}\n`;
    });
  }
  return out.trim();
}

/** What Poradca is doing right now — a verb for the tool. An unknown tool stays a name (better raw than invented). */
const STEP_VERBS: Record<string, string> = {
  Read: "Čítam",
  Grep: "Hľadám",
  Glob: "Prechádzam súbory",
  stavba: "Pozerám stav stavby",
  plan_uloh: "Pozerám plán úloh",
  git_historia: "Pozerám históriu zmien",
  git_zmena: "Pozerám zmenu",
  zaznam_agenta: "Rozoberám kroky agenta stavby",
  kontajnery: "Pozerám kontajnery projektu",
  logy: "Čítam log",
  ci: "Pozerám zostavenie (CI)",
  znalostna_baza: "Hľadám v Znalostnej báze",
  znalostna_baza_dokument: "Čítam dokument zo Znalostnej bázy",
  databaza_uat: "Pýtam sa databázy UAT",
  rad: "Čakám v rade",
};

export function stepLabel(tool: string, target: string): string {
  const verb = STEP_VERBS[tool] ?? tool;
  return target ? `${verb} — ${target}` : verb;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  return rest ? `${m} min ${rest} s` : `${m} min`;
}

/** Cost of the answer by the same price list as Costs (ICCINT-168). Without it the model price list or the rate is not
 *  yet known, or it is an old answer without a session log — never „nezadaná cena". */
export function formatCost(eur: number | null | undefined): string {
  if (eur == null) return "cena sa zatiaľ nedá vyčísliť";
  return `${eur.toLocaleString("sk-SK", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
}

// The „what we are talking about" choice — a version with the phase and build state.
const STAGE_LABELS: Record<string, string> = {
  priprava: "príprava",
  navrh: "návrh",
  vizual: "vizuál",
  programovanie: "programovanie",
  verifikacia: "verifikácia",
  done: "hotová",
};
const STATUS_LABELS: Record<string, string> = {
  agent_working: "agent pracuje",
  awaiting_manazer: "čaká na Manažéra",
  blocked: "zastavená",
  paused: "pozastavená",
  done: "hotová",
};

export function versionOptionLabel(v: PoradcaVersionInfo): string {
  if (!v.stage) return `${v.version_number} — stavba nezačala`;
  const stage = STAGE_LABELS[v.stage] ?? v.stage;
  const status = v.status ? (STATUS_LABELS[v.status] ?? v.status) : "";
  return v.stage === "done" ? `${v.version_number} — hotová` : `${v.version_number} — ${stage}, ${status}`;
}
