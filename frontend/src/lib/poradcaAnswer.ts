// Ako sa odpoveď Poradcu ukáže človeku (ICCINT-167). Čisté funkcie — skúšky v `test_poradcaAnswer`.

import type { PoradcaVersionInfo } from "@/types/poradca";

/** Bloky z charty Poradcu (časť 4) a nadpis, pod ktorým ich človek uvidí. Kokpit z nich robí tlačidlá. */
const BLOCKS: { tag: string; heading: string }[] = [
  { tag: "pokyn-pre-agenta", heading: "Pokyn pre agenta stavby" },
  { tag: "poziadavka-do-zasobnika", heading: "Požiadavka do Zásobníka" },
  // DEV-29: answers from before the change carry the old block — it, too, only ever goes to the Zásobník.
  { tag: "poziadavka-na-novu-verziu", heading: "Požiadavka do Zásobníka" },
];

/**
 * Text odpovede na zobrazenie: bloky so značkami sa zmenia na citát s nadpisom. Bez toho by ich vykresľovač
 * Markdownu (HTML neprepúšťa) zahodil spolu s obsahom a človek by nevidel, čo tlačidlo vloží.
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

/** Čo Poradca práve robí — sloveso k nástroju. Neznámy nástroj ostane menom (radšej surové než vymyslené). */
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

/** Cena odpovede tým istým cenníkom ako Náklady (ICCINT-168). Bez nej cenník modelu alebo kurz ešte nie je
 *  zistený, alebo ide o starú odpoveď bez záznamu sedenia — nikdy nie „nezadaná cena". */
export function formatCost(eur: number | null | undefined): string {
  if (eur == null) return "cena sa zatiaľ nedá vyčísliť";
  return `${eur.toLocaleString("sk-SK", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
}

// Voľba „o čom sa rozprávame" — verzia s fázou a stavom stavby.
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
