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
