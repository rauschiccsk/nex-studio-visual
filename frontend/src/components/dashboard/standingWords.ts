/**
 * DEV-150 — the sentences of „Kde stojíme“, worded from the cockpit's records with the labels Riadiace centrum uses.
 * Never the agent's own text: the facts come from the build's state, the plan and the deploys.
 */
import { BLOCK_REASON_LABELS, PHASE_LABELS, type BuildPhase } from "@/components/cockpit/labels";
import type { BlockReason } from "@/services/api/pipeline";
import type { OpenVersionStanding, ProjectStanding } from "@/services/api/standing";
import { formatDayMonth } from "@/utils/format";

const DAY_MS = 24 * 60 * 60 * 1000;

/** „dnes“, „1 deň“, „3 dni“, „5 dní“ — how long a build has waited. @internal — exported for its test (DEV-155). */
export function daysWords(since: string, now: number = Date.now()): string {
  const days = Math.max(0, Math.floor((now - new Date(since).getTime()) / DAY_MS));
  if (days === 0) return "dnes";
  if (days === 1) return "1 deň";
  return days < 5 ? `${days} dni` : `${days} dní`;
}

const phase = (stage: string): string => PHASE_LABELS[stage as BuildPhase] ?? stage;

/** Who is on turn and since when — „Čaká na teba od 15.10. (5 dní)“, „Agent pracuje“, „Pozastavené“. */
export function turnWords(v: OpenVersionStanding, now: number = Date.now()): string {
  if (v.status === "agent_working") return "Agent pracuje";
  if (v.status === "paused") return "Pozastavené";
  if (v.waiting_since) return `Čaká na teba od ${formatDayMonth(v.waiting_since)} (${daysWords(v.waiting_since, now)})`;
  return "Čaká na teba";
}

/** What it takes to carry on, in a person's words — never the agent's own text. */
export function nextWords(v: OpenVersionStanding): string {
  if (v.status === "blocked") {
    const why = v.block_reason ? BLOCK_REASON_LABELS[v.block_reason as BlockReason] : undefined;
    return `${why ?? "Stavba stojí"} — pokračuje sa v Riadiacom centre.`;
  }
  if (v.status === "awaiting_manazer") return `Čaká na tvoje schválenie fázy ${phase(v.stage)} v Riadiacom centre.`;
  if (v.status === "paused") return "Stavbu si pozastavil — pokračuje sa v Riadiacom centre.";
  return "Netreba nič — agent pracuje.";
}

/**
 * „z“ or „zo“ before a number, as it is read: z piatich, zo šiestich, zo šestnástich, zo sto. The preposition takes
 * „zo“ before the sounds s, z, š, ž — the first word of the number decides.
 * @internal — exported for its test (DEV-155).
 */
export function zOrZo(n: number): string {
  const starts = (first: number) => [4, 6, 7].includes(first);
  if (n < 20) return [4, 6, 7, 14, 16, 17].includes(n) ? "zo" : "z";
  if (n < 100) return starts(Math.floor(n / 10)) ? "zo" : "z";
  if (n < 1000) return Math.floor(n / 100) === 1 || starts(Math.floor(n / 100)) ? "zo" : "z";
  return "z";
}

/** „hotová 1“, „hotové 3“, „hotových 7“ — the word agrees with the number. */
function doneWords(n: number): string {
  if (n === 1) return "hotová 1";
  if (n >= 2 && n <= 4) return `hotové ${n}`;
  return `hotových ${n}`;
}

/** „NEX Manager 1.3.0 — Programovanie, hotových 7 zo 16 úloh“ */
export function versionWords(project: ProjectStanding, v: OpenVersionStanding): string {
  const tasks = v.tasks_total
    ? `, ${doneWords(v.tasks_done)} ${zOrZo(v.tasks_total)} ${v.tasks_total} ${v.tasks_total === 1 ? "úlohy" : "úloh"}`
    : "";
  const kind = v.fast_fix ? " (rýchla oprava)" : "";
  return `${project.name} ${v.version_number}${kind} — ${phase(v.stage)}${tasks}`;
}

export const prodWords = (project: ProjectStanding): string =>
  project.prod_version ? `Na PROD je ${project.prod_version}.` : "Na PROD zatiaľ nie je nič.";

