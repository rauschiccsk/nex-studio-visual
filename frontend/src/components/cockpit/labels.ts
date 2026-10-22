// Slovak human-facing display labels for the cockpit (CR-NS-018).
//
// Display layer only — the machine values (current_stage, current_actor, author,
// recipient enums) are unchanged. A single shared map per dimension keeps the
// rail, the agent chips, and the message bubbles consistent (no duplicated
// literals). Director feedback: internal codes + English roles aren't
// understandable, especially for Directors who don't know the ICC methodology.

import type { VersionStatus } from "@/types/version";
import type {
  BlockReason,
  PipelineParticipant,
} from "../../services/api/pipeline";

// ── v2.0.0 vocabulary (CR-V2-019 / CR-V2-021) ─────────────────────────────────
// The v2 build pipeline is visible as FOUR phases (design §2.1) — the display now
// shows *which stage the AI Agent is in*, not *which agent is active*. This is the
// canonical v2 vocabulary; the Vývoj 4-phase board (CR-V2-021) and the AI Agent
// tab strip (CR-V2-022) read it. Owned end-to-end by ONE doer (the AI Agent) and
// checked by the independent Auditor — three participants only (design §1/§4.1):
// AI Agent (does the work), Auditor (independent verifier), Manažér (approves).
//
// CR-V2-021 completed the collapse: the FE openapi-typescript regen flipped the
// generated PipelineStage/PipelineActor enums to v2, so ROLE_LABELS below now keys on the
// 3 participants and the phase maps on the 4 phases, and the v1 Coordinator/
// triage/regate label maps + the 11-stage STAGE_ORDER family are removed (see the NOTEs
// below). The tone palette (TONE_*/StatusTone) is intact (salvaged).

// The v2 build phase machine value (mirrors design §2.1; `done` is the terminal phase). CR-1
// (nex-studio-visual) inserts `vizual` (the live-preview phase) between `navrh` and `programovanie`.
export type BuildPhase =
  "priprava" | "navrh" | "vizual" | "programovanie" | "verifikacia" | "done";

// Slovak human-facing label per v2 build phase — the 4-phase Vývoj board chips + the
// AI Agent tab strip. Collapses the v1 11-stage STAGE_LABELS to the four real phases.
export const PHASE_LABELS: Record<BuildPhase, string> = {
  priprava: "Príprava",
  navrh: "Návrh",
  vizual: "Vizuál",
  programovanie: "Programovanie",
  verifikacia: "Verifikácia",
  done: "Hotovo",
};

// Canonical v2 phase order — the horizontal phase bar (Príprava › Návrh › Vizuál › Programovanie
// › Verifikácia › Hotovo). Replaces the v1 STAGE_ORDER for v2 surfaces.
export const PHASE_ORDER: BuildPhase[] = [
  "priprava",
  "navrh",
  "vizual",
  "programovanie",
  "verifikacia",
  "done",
];

// Slovak label per pipeline participant — the v2 3-role vocabulary (AI Agent / Auditor / Manažér + the
// system author). ``manazer`` is the human operator; ``system`` authors notices. (No Coordinator / Designer
// / Customer / Implementer — those v1 roles collapsed into the single AI Agent, CR-V2-001/007.)
export const ROLE_LABELS: Record<PipelineParticipant, string> = {
  ai_agent: "AI Agent",
  auditor: "Audítor",
  manazer: "Manažér",
  system: "Systém",
  // ICCINT-12: our technical team, answering an agent's framework_issue escalation.
  dedo: "Dedo",
};

// NOTE (CR-V2-021): the v1 Coordinator message-badge labels (SYNTHESIS_LABEL / RAW_REPORT_LABEL /
// AUTONOMOUS_LABEL / DIRECTOR_BRIEF_LABEL — the synthesis/raw-report/autonomous/Director-brief markers on the
// retired message-bubble thread) are REMOVED with the bubble thread + WhosTurnBoard; the v2 board uses durable
// phase artifacts + the who's-up status instead. The Coordinator no longer authors any message (design §2.2).

// R4 (D1/D2): Slovak phrase per block_reason — the precise reason a pipeline is `blocked`, so the Director
// distinguishes an agent QUESTION from an agent ERROR from a SYSTEM error from a parse failure at a glance.
export const BLOCK_REASON_LABELS: Record<BlockReason, string> = {
  agent_question: "Agent sa pýta",
  decision_needed: "Treba tvoje rozhodnutie", // CR-V2-041: an interactive consultation (Decision Cards)
  agent_error: "Agent zlyhal",
  system_error: "Systémová chyba",
  parse_exhaustion: "Chyba spracovania výstupu",
  // ICCINT-43: a check the ENGINE ran came back negative. Nobody failed — the work simply is not finished.
  // Every other label here names a culprit, which is why this needed its own: on 29.08.2026 the boot re-check
  // borrowed `agent_error` and the screen read "Niečo zlyhalo — Agent zlyhal" over an agent whose fix worked.
  check_failed: "Kontrola neprešla",
  // DEV-7: the agent needs a database change only Ri may approve — a card, not a free-text question.
  schema_approval: "Treba schváliť štruktúru databázy",
  // A framework_issue means the bug is in NEX Studio itself (not the project) — our technical team resolves it.
  // Plain Slovak, no internal "Dedo"/"framework" jargon (a non-expert Manažér doesn't know who "Dedo" is). The
  // Manažér's one concrete move is "Nahlásiť znova" (NahlasitZnovaBar); it reads as a system/blocked red state.
  framework_issue: "NEX Studio má chybu — rieši ju náš technický tím",
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

// Slovak display label per task_type — the tiny tag on a Plán úloh / task-plan leaf. Keeps the badge readable
// for a non-expert Manažér: no raw English "backend"/"frontend"/"migration" tokens. Unknown → the raw value.
export const TASK_TYPE_LABELS: Record<string, string> = {
  backend: "Backend",
  frontend: "Frontend",
  migration: "Databáza",
  test: "Test",
  docs: "Dokumentácia",
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
  green:
    "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200",
  blue: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-200",
  amber:
    "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-200",
  red: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-200",
  neutral:
    "border-[var(--color-border-default)] bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]",
};

// Honest verification (audit honest #6): a version can be `verified === true` yet its verification could NOT
// actually be CONFIRMED — the PASS was never anchored to a commit (`unbound` / `hotovo_unbound`, signed while
// the repo was unreadable) or the repo can't be read right now (`repo_unreadable`). For those three the honest
// surface is an AMBER "overenie sa nedá potvrdiť", never a green "overená". The confirmed-green provenances
// (`sha_match` / `hotovo_match` / `released`) stay green; the drift ones (`sha_drift` / `hotovo_drift`) already
// read as a stale-PASS warning via ReverifyBar.
const VERIFICATION_UNCONFIRMED_PROVENANCES = [
  "unbound",
  "repo_unreadable",
  "hotovo_unbound",
] as const;

export function verificationUnconfirmed(
  provenance: string | null | undefined,
): boolean {
  return (
    provenance != null &&
    (VERIFICATION_UNCONFIRMED_PROVENANCES as readonly string[]).includes(
      provenance,
    )
  );
}

// v4.0.56 — the DRIFT sibling, deliberately NOT folded into `verificationUnconfirmed`: "unconfirmed" means we
// CANNOT TELL whether the check still holds, while drift means we KNOW it no longer does, and the two have
// different remedies (verify before deploying vs. "Over znova" re-runs it for you). The comment above used to
// argue drift needed no surface here because ReverifyBar warns about it — but ReverifyBar sits BELOW the
// thread, while the status strip and the Plán úloh card kept reading a confident green "pripravené na
// nasadenie" with a button into the paused deploy screen. Three signals, two of them wrong (Director report,
// 2026-07-26). Whatever reads `verificationUnconfirmed` for an honest surface must read this too.
const VERIFICATION_DRIFTED_PROVENANCES = [
  "sha_drift",
  "hotovo_drift",
] as const;

/**
 * DEV-51 — how the project's checks (CI) on the code that would be deployed stand against a deploy. The backend
 * decides (`deploy.ci_deploy_cause` closes Nasadiť and refuses the deploy itself); this mirrors that rule only
 * so Riadiace centrum never says "pripravené na nasadenie" next to a failed build. Red blocks; a run still
 * going blocks until it finishes; not knowing does not — and says so in its own line (StavZostavenia).
 */
export type CiDeployBlock = "red" | "running" | null;

export function ciDeployBlock(ci: { stav: string; bezi?: boolean } | null | undefined): CiDeployBlock {
  if (!ci) return null;
  if (ci.stav === "red") return "red";
  if (ci.bezi) return "running";
  return null;
}

export function verificationDrifted(
  provenance: string | null | undefined,
): boolean {
  return (
    provenance != null &&
    (VERIFICATION_DRIFTED_PROVENANCES as readonly string[]).includes(provenance)
  );
}

// NOTE (CR-V2-021): the v1 ``STAGE_ORDER`` / ``FAST_FIX_STAGE_ORDER`` / ``stageOrderForFlow`` /
// ``REGATE_TARGETS`` / ``nextStageLabel`` (the 11-stage waterfall order + gate_g re-gate targets) are
// REMOVED — the v2 Vývoj board uses the 4-phase :data:`PHASE_ORDER` instead.

// ─── Stav VERZIE ──────────────────────────────────────────────────────────────
//
// ICCINT-50 (03.09.2026): jedno miesto pre obe stránky, ktoré stav verzie zobrazujú
// (ProjectDetailPage, VersionDetailPage). Dovtedy mala každá vlastnú kópiu tých istých
// dvoch funkcií — a tak vzniká, že sa oprava spraví na jednej a druhá zostane pozadu.
//
// ``done`` pribudlo preto, že postavená a schválená verzia niesla ``planned`` a na obrazovke
// sa nedala odlíšiť od nezačatej. Ponechať ju ``active`` až do nasadenia by ju zase nedalo
// odlíšiť od tej, ktorá sa práve stavia.
// DEV-134: typed by the status the backend sends — a new status fails the type check here, and the Zásobník reads
// its labels from this one place instead of keeping its own copy (its copy missed „done“ → „Neznámy stav“).
const VERSION_STATUS_LABELS: Record<VersionStatus, string> = {
  planned: "Plánované",
  active: "Prebieha",
  done: "Hotové",
  released: "Vydané",
};

/** Popis stavu verzie. Neznámy stav sa NEVYDÁVA za „Plánované" — vypíše sa, ako prišiel.
 *  Prepadové „Plánované" je presne ten druh tichej lži, kvôli ktorej vznikol ICCINT-50. */
export function versionStatusLabel(status: string): string {
  return VERSION_STATUS_LABELS[status as VersionStatus] ?? status;
}

/** Farebné ladenie štítku. ``done`` má vlastný odtieň — hotové a vydané sú dva rôzne stavy
 *  a nemajú vyzerať rovnako; nasadenie je samostatný krok, ktorý ešte neprebehol. */
export function versionStatusCls(status: string): string {
  if (status === "active")
    return "bg-[var(--color-state-warning-bg)] border border-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]";
  if (status === "done")
    return "bg-[var(--color-state-info-bg)] border border-[var(--color-state-info-bg)] text-[var(--color-state-info-fg)]";
  if (status === "released")
    return "bg-[var(--color-state-success-bg)] border border-[var(--color-state-success-bg)] text-[var(--color-state-success-fg)]";
  return "bg-[var(--color-surface-active)] border border-[var(--color-border-strong)] text-[var(--color-text-secondary)]";
}
