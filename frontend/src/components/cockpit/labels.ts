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
