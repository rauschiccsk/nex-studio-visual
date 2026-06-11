// Slovak human-facing display labels for the cockpit (CR-NS-018).
//
// Display layer only — the machine values (current_stage, current_actor, author,
// recipient enums) are unchanged. A single shared map per dimension keeps the
// rail, the agent chips, and the message bubbles consistent (no duplicated
// literals). Director feedback: internal codes + English roles aren't
// understandable, especially for Directors who don't know the ICC methodology.

import type { PipelineParticipant, PipelineStage } from "../../services/api/pipeline";

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

export const TONE_TEXT: Record<StatusTone, string> = {
  green: "text-emerald-400",
  blue: "text-sky-400",
  amber: "text-amber-400",
  red: "text-red-400",
  neutral: "text-slate-600",
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
};

export const TONE_BANNER: Record<StatusTone, string> = {
  green: "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
  blue: "border-sky-500/40 bg-sky-500/10 text-sky-200",
  amber: "border-amber-500/40 bg-amber-500/10 text-amber-200",
  red: "border-red-500/40 bg-red-500/10 text-red-200",
  neutral: "border-slate-600/40 bg-slate-700/10 text-slate-300",
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

// Human label of the stage that follows `stage` (clamped at the last). Drives the
// "Schváliť → spustí sa ďalšia fáza (…)" consequence line.
export function nextStageLabel(stage: PipelineStage): string {
  const idx = STAGE_ORDER.indexOf(stage);
  const next = idx >= 0 ? STAGE_ORDER[Math.min(idx + 1, STAGE_ORDER.length - 1)] : undefined;
  return next ? STAGE_LABELS[next] : STAGE_LABELS[stage];
}
