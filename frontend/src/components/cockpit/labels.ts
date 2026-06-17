// Slovak human-facing display labels for the cockpit (CR-NS-018).
//
// Display layer only — the machine values (current_stage, current_actor, author,
// recipient enums) are unchanged. A single shared map per dimension keeps the
// rail, the agent chips, and the message bubbles consistent (no duplicated
// literals). Director feedback: internal codes + English roles aren't
// understandable, especially for Directors who don't know the ICC methodology.

import type { BlockReason, PipelineParticipant, PipelineStage } from "../../services/api/pipeline";

export const STAGE_LABELS: Record<PipelineStage, string> = {
  kickoff: "Príprava",
  gate_a: "Rozsah",
  gate_b: "Rozhranie (API)",
  gate_c: "Backend návrh",
  gate_d: "Frontend návrh",
  gate_e: "Kontrola zákazníkom",
  task_plan: "Plán úloh",
  build: "Programovanie",
  gate_g: "Audit",
  release: "Vydanie",
  done: "Hotovo",
};

// Raw machine code per stage — usable as a hover tooltip alongside the label.
export const STAGE_CODES: Record<PipelineStage, string> = {
  kickoff: "kickoff",
  gate_a: "Gate A",
  gate_b: "Gate B",
  gate_c: "Gate C",
  gate_d: "Gate D",
  gate_e: "Gate E",
  task_plan: "task_plan",
  build: "build",
  gate_g: "Gate G",
  release: "release",
  done: "done",
};

export const ROLE_LABELS: Record<PipelineParticipant, string> = {
  coordinator: "Koordinátor",
  designer: "Návrhár",
  customer: "Zákazník",
  implementer: "Programátor",
  auditor: "Audítor",
  director: "Director",
  system: "Systém",
};

// CR-NS-053 Pillar A: the Coordinator's Director-facing synthesis (payload.is_synthesis) is the PRIMARY
// message at each decision point — its badge label. The raw worker report it summarizes stays in the
// thread as a secondary, dimmed "pôvodný report" (drill-down audit trail; never removed).
export const SYNTHESIS_LABEL = "Zhrnutie";
export const RAW_REPORT_LABEL = "pôvodný report";

// CR-NS-055 Pillar B: an AUTONOMOUS Coordinator decision (payload.is_autonomous) auto-executed a bounded
// recovery without a Director click — the Director SEES it (never silent), badged distinctly.
export const AUTONOMOUS_LABEL = "Koordinátor rozhodol";

// R4 (D1/D2): Slovak phrase per block_reason — the precise reason a pipeline is `blocked`, so the Director
// distinguishes an agent QUESTION from an agent ERROR from a SYSTEM error from a parse failure at a glance.
export const BLOCK_REASON_LABELS: Record<BlockReason, string> = {
  agent_question: "Agent sa pýta",
  agent_error: "Agent zlyhal",
  system_error: "Systémová chyba",
  parse_exhaustion: "Chyba spracovania výstupu",
};

// Slovak labels for EPIC/FEAT/TASK node statuses in the TaskPlanPanel tree (CR-NS-020 CR-5).
// Union of epic (planned/in_progress/done) + feat/task (todo/in_progress/done/failed).
export const TASK_STATUS_LABELS: Record<string, string> = {
  planned: "Naplánované",
  todo: "Čaká",
  in_progress: "Prebieha",
  done: "Hotovo",
  failed: "Zlyhalo",
};

// ── Unified cockpit status palette (CR-NS-028) ────────────────────────────────
// ONE colour means exactly one thing across the whole cockpit, so it can't drift:
//   green (emerald) = done / ok / pass
//   blue  (sky)     = in_progress / working / currently active
//   amber (yellow)  = waiting / todo / planned / awaiting_director
//   red             = error / fail / blocked
//   neutral (slate) = idle / inactive
// Components map a status → a tone here (single source of truth), then a tone → their
// own class shape (dot / text / banner) via the TONE_* maps below.
export type StatusTone = "green" | "blue" | "amber" | "red" | "neutral";

// Task/node lifecycle status (tasks.status, and derived feat/epic) → tone.
export const TASK_STATUS_TONE: Record<string, StatusTone> = {
  done: "green",
  in_progress: "blue",
  planned: "amber",
  todo: "amber",
  failed: "red",
};

// Pipeline state status (pipeline_state.status) → tone.
export const PIPELINE_STATUS_TONE: Record<string, StatusTone> = {
  agent_working: "blue",
  awaiting_director: "amber",
  blocked: "red",
  paused: "amber", // waiting on the Director to resume/end (CR-NS-035)
  done: "green",
};

// Tone → class shape. Centralising the colour VALUES too (not just the semantic
// assignment) keeps "blue" the same blue everywhere.
export const TONE_DOT: Record<StatusTone, string> = {
  green: "bg-emerald-500",
  blue: "bg-sky-500",
  amber: "bg-amber-400",
  red: "bg-red-500",
  neutral: "bg-slate-500",
};

// CR-NS-067c: light-readable + dark-identical (`text-X-600 dark:text-X-400`); the -400
// status colors were too faint on a white surface in light mode.
export const TONE_TEXT: Record<StatusTone, string> = {
  green: "text-emerald-600 dark:text-emerald-400",
  blue: "text-sky-600 dark:text-sky-400",
  amber: "text-amber-600 dark:text-amber-400",
  red: "text-red-600 dark:text-red-400",
  neutral: "text-[var(--color-text-muted)]",
};

// Coordinator executable-action → Slovak effect phrase (E7, F-008 §5/§9). The build approve button is
// labelled "Schváliť Koordinátorov návrh (<effect>)" so it names the concrete effect (WS-C class-D),
// never a generic "Schváliť".
export const COORDINATOR_ACTION_LABELS: Record<string, string> = {
  coordinator_reset_task: "reštartovať úlohu",
  coordinator_move_baseline: "posunúť baseline",
  coordinator_clear_session: "vyčistiť session",
  coordinator_escalate_dedo: "eskalovať Dedovi",
  coordinator_route_to_designer: "opraviť spec cez Návrhára",
  capture_backlog_item: "Zaevidovať do backlogu",
};

// R4 (D3): Coordinator triage_class → Slovak phrase, so the board's "Koordinátor klasifikoval: X" line reads
// legibly for a non-Dedo Director. Mirrors the BE CoordinatorDirective.triage_class Literal.
export const TRIAGE_CLASS_LABELS: Record<string, string> = {
  spec_problem: "problém v špecifikácii",
  programmer_guidance: "vedenie programátora",
  nex_studio_bug: "chyba NEX Studio",
  director_decision: "rozhodnutie Directora",
  programmer_routine_question: "rutinná otázka programátora",
};

// CR-NS-067c: light-readable + dark-identical (`text-X-700 dark:text-X-200`); the -200
// banner text was near-white and unreadable on the pale tint in light mode.
export const TONE_BANNER: Record<StatusTone, string> = {
  green: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200",
  blue: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-200",
  amber: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-200",
  red: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-200",
  neutral: "border-[var(--color-border-default)] bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]",
};

// Canonical stage order — mirrors backend orchestrator.STAGE_ORDER. Shared so the
// rail and the action bar don't each keep a copy (DRY).
export const STAGE_ORDER: PipelineStage[] = [
  "kickoff",
  "gate_a",
  "gate_b",
  "gate_c",
  "gate_d",
  "gate_e",
  "task_plan",
  "build",
  "gate_g",
  "release",
  "done",
];

// Fast-Fix Lane stage path (F-009, CR-NS-094/095) — mirrors backend orchestrator.FAST_FIX_STAGE_ORDER.
// The lightweight lane skips the full waterfall (gate_a-e / task_plan / gate_g): kickoff advances straight
// to build, a settled build to release. A subset of STAGE_ORDER, so STAGE_LABELS / STAGE_CODES already
// cover every member.
export const FAST_FIX_STAGE_ORDER: PipelineStage[] = ["kickoff", "build", "release", "done"];

// The stage order for a given pipeline flow_type. fast_fix runs the short lane; every other flow
// (new_version / cr / bug) traverses the full STAGE_ORDER (F-009 §3). Default new_version.
export function stageOrderForFlow(flowType?: string): PipelineStage[] {
  return flowType === "fast_fix" ? FAST_FIX_STAGE_ORDER : STAGE_ORDER;
}

// Slovak display label per pipeline flow_type (F-009). The fast-fix lane is badged on the board so it
// reads distinctly from a full-waterfall version; the map covers all flows for reuse/consistency.
export const FLOW_LABELS: Record<string, string> = {
  new_version: "Nová verzia",
  cr: "Zmena (CR)",
  bug: "Oprava chyby",
  fast_fix: "Rýchla oprava",
};

// CR-NS-057 §F2.4: the stages a gate_g FAIL can re-gate to (override chips). Excludes kickoff / release /
// done / gate_g — only the design + build stages (gate_a..build) are valid re-gate targets.
export const REGATE_TARGETS: PipelineStage[] = STAGE_ORDER.filter(
  (s) => s !== "kickoff" && s !== "release" && s !== "done" && s !== "gate_g",
);

// Human label of the stage that follows `stage` in the given flow (clamped at the last). Drives the
// "Schváliť → spustí sa ďalšia fáza (…)" consequence line. Flow-aware so a fast_fix kickoff correctly
// reads "Programovanie" (build), not "Rozsah" (gate_a) — that gate is skipped in the short lane.
export function nextStageLabel(stage: PipelineStage, flowType?: string): string {
  const order = stageOrderForFlow(flowType);
  const idx = order.indexOf(stage);
  const next = idx >= 0 ? order[Math.min(idx + 1, order.length - 1)] : undefined;
  return next ? STAGE_LABELS[next] : STAGE_LABELS[stage];
}
