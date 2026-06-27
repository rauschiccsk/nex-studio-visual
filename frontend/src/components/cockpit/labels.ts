// Slovak human-facing display labels for the cockpit (CR-NS-018).
//
// Display layer only — the machine values (current_stage, current_actor, author,
// recipient enums) are unchanged. A single shared map per dimension keeps the
// rail, the agent chips, and the message bubbles consistent (no duplicated
// literals). Director feedback: internal codes + English roles aren't
// understandable, especially for Directors who don't know the ICC methodology.

import type { BlockReason, PipelineParticipant, PipelineStage } from "../../services/api/pipeline";

// ── v2.0.0 vocabulary (CR-V2-019 / CR-V2-021) ─────────────────────────────────
// The v2 build pipeline is visible as FOUR phases (design §2.1) — the display now
// shows *which stage the AI Agent is in*, not *which agent is active*. This is the
// canonical v2 vocabulary; the Vývoj 4-phase board (CR-V2-021) and the AI Agent
// tab strip (CR-V2-022) read it. Owned end-to-end by ONE doer (the AI Agent) and
// checked by the independent Auditor — three participants only (design §1/§4.1):
// AI Agent (does the work), Auditor (independent verifier), Manažér (approves).
//
// CR-V2-021 completed the collapse: the FE openapi-typescript regen flipped the
// generated PipelineStage/PipelineActor enums to v2, so STAGE_LABELS / STAGE_CODES /
// ROLE_LABELS below now key on the 4 phases / 3 participants, and the v1 Coordinator/
// triage/regate label maps + the 11-stage STAGE_ORDER family are removed (see the NOTEs
// below). The tone palette (TONE_*/StatusTone/DECISION_BANNER) is intact (salvaged).

// The v2 build phase machine value (mirrors design §2.1; `done` is the terminal phase).
export type BuildPhase = "priprava" | "navrh" | "programovanie" | "verifikacia" | "done";

// Slovak human-facing label per v2 build phase — the 4-phase Vývoj board chips + the
// AI Agent tab strip. Collapses the v1 11-stage STAGE_LABELS to the four real phases.
export const PHASE_LABELS: Record<BuildPhase, string> = {
  priprava: "Príprava",
  navrh: "Návrh",
  programovanie: "Programovanie",
  verifikacia: "Verifikácia",
  done: "Hotovo",
};

// Canonical v2 phase order — the horizontal phase bar (Príprava › Návrh › Programovanie
// › Verifikácia › Hotovo). Replaces the v1 STAGE_ORDER for v2 surfaces.
export const PHASE_ORDER: BuildPhase[] = ["priprava", "navrh", "programovanie", "verifikacia", "done"];

// Raw machine code per phase — usable as a hover tooltip alongside the label.
export const PHASE_CODES: Record<BuildPhase, string> = {
  priprava: "priprava",
  navrh: "navrh",
  programovanie: "programovanie",
  verifikacia: "verifikacia",
  done: "done",
};

// The v2 pipeline participant machine value — exactly three (design §1/§4.1): the AI
// Agent does the whole build, the Auditor independently verifies, the Manažér approves.
// (No Coordinator / Designer / Customer / Implementer — those v1 roles collapse into
// the single AI Agent; `system` stays for system-authored notices.)
export type V2Participant = "ai_agent" | "auditor" | "manazer" | "system";

// Slovak label per v2 participant — the 3-role vocabulary. Replaces the v1 7-role
// ROLE_LABELS for v2 surfaces (who's-up status, the AI Agent header, message bubbles).
export const V2_ROLE_LABELS: Record<V2Participant, string> = {
  ai_agent: "AI Agent",
  auditor: "Audítor",
  manazer: "Manažér",
  system: "Systém",
};

// Human label of the phase that follows `phase` (clamped at the terminal `done`). Drives
// the "Schváliť → spustí sa ďalšia fáza (…)" consequence line on the v2 board.
export function nextPhaseLabel(phase: BuildPhase): string {
  const idx = PHASE_ORDER.indexOf(phase);
  const next = idx >= 0 ? PHASE_ORDER[Math.min(idx + 1, PHASE_ORDER.length - 1)] : undefined;
  return next ? PHASE_LABELS[next] : PHASE_LABELS[phase];
}

// ── STAGE / ROLE maps over the (now v2) generated enums (CR-V2-021) ──────────────────────
// The FE openapi-typescript regen flipped ``PipelineStage`` → the 4 phases and ``PipelineActor`` → the two
// v2 agents (CR-V2-021's dependency, per the note above). These maps therefore now key on the v2 enums.
// ``STAGE_LABELS`` / ``STAGE_CODES`` collapse to the four phases (they ARE :data:`PHASE_LABELS` /
// :data:`PHASE_CODES` now — kept as named exports so the still-v1 consumers compile until their own CRs
// re-home them onto the v2 names); ``ROLE_LABELS`` collapses to the v2 participants.
export const STAGE_LABELS: Record<PipelineStage, string> = PHASE_LABELS;

// Raw machine code per phase — usable as a hover tooltip alongside the label.
export const STAGE_CODES: Record<PipelineStage, string> = PHASE_CODES;

// Slovak label per pipeline participant — the v2 3-role vocabulary (AI Agent / Auditor / Manažér + the
// system author). ``manazer`` is the human operator; ``system`` authors notices. (No Coordinator / Designer
// / Customer / Implementer — those v1 roles collapsed into the single AI Agent, CR-V2-001/007.)
export const ROLE_LABELS: Record<PipelineParticipant, string> = {
  ai_agent: "AI Agent",
  auditor: "Audítor",
  manazer: "Manažér",
  system: "Systém",
};

// NOTE (CR-V2-021): the v1 Coordinator message-badge labels (SYNTHESIS_LABEL / RAW_REPORT_LABEL /
// AUTONOMOUS_LABEL / DIRECTOR_BRIEF_LABEL — the synthesis/raw-report/autonomous/Director-brief markers on the
// retired message-bubble thread) are REMOVED with the bubble thread + WhosTurnBoard; the v2 board uses durable
// phase artifacts + the who's-up status instead. The Coordinator no longer authors any message (design §2.2).

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
//   amber (yellow)  = waiting / todo / planned / awaiting_manazer
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
  awaiting_manazer: "amber",
  blocked: "red",
  paused: "amber", // waiting on the Manažér to resume/end (CR-NS-035)
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

// NOTE (CR-V2-021): the v1 ``COORDINATOR_ACTION_LABELS`` (executable-action → effect phrase) +
// ``TRIAGE_CLASS_LABELS`` (Coordinator triage_class → Slovak) are REMOVED — the Coordinator hub is gone
// (design §2.2); there are no Coordinator directives / triage classes to label on the v2 board.

// CR-NS-067c: light-readable + dark-identical (`text-X-700 dark:text-X-200`); the -200
// banner text was near-white and unreadable on the pale tint in light mode.
export const TONE_BANNER: Record<StatusTone, string> = {
  green: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200",
  blue: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-200",
  amber: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-200",
  red: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-200",
  neutral: "border-[var(--color-border-default)] bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]",
};

// CR-2 (v0.7.3): the HIGH-CONTRAST sticky decision CTA banner — used (instead of the low-key TONE_BANNER) only
// when status is awaiting_manazer / blocked, so a "your turn" board never reads as "stuck". Solid state-token
// bg + fg + a left accent in the same fg (token-disciplined: the shared --color-state-* pairs carry light+dark,
// no raw pastels). Tone-aware so it stays inside the unified palette (CR-NS-028): amber = awaiting, red = blocked.
export const DECISION_BANNER: Partial<Record<StatusTone, string>> = {
  amber:
    "bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)] border-[var(--color-state-warning-fg)]",
  red: "bg-[var(--color-state-error-bg)] text-[var(--color-state-error-fg)] border-[var(--color-state-error-fg)]",
};

// Slovak display label per pipeline flow_type. Only ``new_version`` + ``fast_fix`` survive (OQ-1 dropped
// ``cr``/``bug``); the fast-fix lane is badged on the board so it reads distinctly from a full version.
export const FLOW_LABELS: Record<string, string> = {
  new_version: "Nová verzia",
  fast_fix: "Rýchla oprava",
};

// NOTE (CR-V2-021): the v1 ``STAGE_ORDER`` / ``FAST_FIX_STAGE_ORDER`` / ``stageOrderForFlow`` /
// ``REGATE_TARGETS`` / ``nextStageLabel`` (the 11-stage waterfall order + gate_g re-gate targets) are
// REMOVED — the v2 Vývoj board uses the 4-phase :data:`PHASE_ORDER` + :func:`nextPhaseLabel` instead.
