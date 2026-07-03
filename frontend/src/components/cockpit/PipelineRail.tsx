// Vývoj horizontal 4-phase bar (CR-V2-021, design §4.4.2). The pipeline is a horizontal phase bar at the
// TOP (Príprava ✓ › Návrh ● › Programovanie ○ › Verifikácia ○) whose chips ARE the tabs — there is no
// separate tab row. Two coexisting states ride the bar: ● = where the BUILD currently is (auto-advances),
// highlighted = which tab the Manažér is VIEWING (their click); these can differ.

import type { ActivityLine, AgentSession, PipelineActor, PipelineBoard, PipelineState } from "../../services/api/pipeline";
import type { BuildPhase, StatusTone } from "./labels";
import { PHASE_CODES, PHASE_LABELS, PHASE_ORDER, TONE_TEXT, V2_ROLE_LABELS } from "./labels";

// The agent actually active: while working = the real streaming role (latest activity frame, fallback the
// stage actor); at rest = who just acted (latest non-manazer/system message author). Used for the who's-up
// status; NOT the nominal current_actor. (Carried from CR-NS-018; re-keyed to the v2 participants.)
export function deriveActiveAgent(board: PipelineBoard | null, activity: ActivityLine[]): PipelineActor | null {
  const state = board?.state ?? null;
  if (!state) return null;
  if (state.status === "agent_working") {
    const last = activity[activity.length - 1];
    return (last?.actor as PipelineActor) ?? state.current_actor;
  }
  if (state.status === "awaiting_manazer" || state.status === "blocked") {
    const msgs = board?.recent_messages ?? [];
    const author = msgs[msgs.length - 1]?.author;
    return author && author !== "manazer" && author !== "system" ? (author as PipelineActor) : null;
  }
  return null;
}

// The four real phases (Hotovo is the terminal sentinel, not a clickable tab — design §4.4.2).
const TAB_PHASES: BuildPhase[] = PHASE_ORDER.filter((p) => p !== "done");

// A phase's position relative to the build's current phase → its chip marker + tone.
//   done (✓, before the build position, or the terminal Hotovo) · current (●, the build position) ·
//   pending (○, not yet reached).
type PhaseMark = "done" | "current" | "pending";

function phaseMarkFor(phase: BuildPhase, state: PipelineState | null): PhaseMark {
  if (!state) return "pending";
  const buildIdx = PHASE_ORDER.indexOf(state.current_stage as BuildPhase);
  const phaseIdx = PHASE_ORDER.indexOf(phase);
  const finished = state.status === "done"; // the whole build reached Hotovo
  if (buildIdx < 0 || phaseIdx < 0) return "pending";
  if (phaseIdx < buildIdx || (phaseIdx === buildIdx && finished)) return "done";
  if (phaseIdx === buildIdx) return "current";
  return "pending";
}

const MARK_GLYPH: Record<PhaseMark, string> = { done: "✓", current: "●", pending: "○" };
const MARK_TONE: Record<PhaseMark, StatusTone> = { done: "green", current: "blue", pending: "neutral" };

interface Props {
  state: PipelineState | null;
  /** Which phase tab the Manažér is currently VIEWING (the highlighted chip) — may differ from ●. */
  viewedPhase: BuildPhase;
  /** Select a phase tab (the chip click). */
  onSelectPhase: (phase: BuildPhase) => void;
}

export function PipelineRail({ state, viewedPhase, onSelectPhase }: Props) {
  return (
    <div className="flex flex-shrink-0 items-center gap-1 overflow-x-auto border-b border-[var(--color-border-default)] px-4 py-2">
      {TAB_PHASES.map((phase, idx) => {
        const mark = phaseMarkFor(phase, state);
        const viewed = phase === viewedPhase;
        const tone = MARK_TONE[mark];
        return (
          <div key={phase} className="flex items-center">
            {idx > 0 && <span className="px-1 text-[var(--color-text-muted)]">›</span>}
            <button
              onClick={() => onSelectPhase(phase)}
              title={PHASE_CODES[phase]}
              aria-current={viewed ? "true" : undefined}
              className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs transition-colors ${
                viewed
                  ? "bg-[var(--color-surface-hover)] font-semibold text-[var(--color-text-primary)] ring-1 ring-[var(--color-accent-primary)]"
                  : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              <span className={`font-mono ${TONE_TEXT[tone]}`} aria-hidden="true">
                {MARK_GLYPH[mark]}
              </span>
              <span>{PHASE_LABELS[phase]}</span>
              {mark === "current" && state?.is_regate && (
                <span className="rounded bg-[var(--color-state-warning-bg)] px-1 text-[9px] text-[var(--color-state-warning-fg)]">
                  oprava #{state.iteration}
                </span>
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ── Who's-up status (design §4.4.2 "Below the tabs") ──────────────────────────
// AI Agent / + helpers / Auditor / čaká na Manažéra — honest, derived from the live state.

interface WhosUpProps {
  state: PipelineState | null;
  activeAgent?: PipelineActor | null;
  agentSessions?: AgentSession[];
  currentTask?: { number: number; title: string } | null;
  // CR-V2-056: the board's LIVE-computed verified provenance. 'sha_drift' = the recorded Verifikácia PASS is
  // stale (the code moved past the verified commit) — we surface a warning so the screen reflects reality, not
  // a frozen green PASS.
  verifiedProvenance?: string;
}

export function WhosUp({ state, activeAgent = null, agentSessions, currentTask, verifiedProvenance }: WhosUpProps) {
  const drifted = verifiedProvenance === "sha_drift";
  // A done build normally hides this line — but a DRIFTED done build still surfaces the stale-PASS warning.
  if (!state || (state.status === "done" && !drifted)) return null;
  const liveness: Partial<Record<PipelineActor, AgentSession["status"]>> = {};
  for (const s of agentSessions ?? []) liveness[s.role] = s.status;

  let label: string;
  let tone: StatusTone;
  if (state.status === "awaiting_manazer" || state.status === "blocked") {
    label = "čaká na Manažéra";
    tone = "amber";
  } else if (state.status === "paused") {
    label = "pozastavené — pokračuj alebo uprav";
    tone = "amber";
  } else {
    // agent_working — name the working agent (+ the Programovanie task in focus, if any).
    const who = activeAgent ? V2_ROLE_LABELS[activeAgent === "auditor" ? "auditor" : "ai_agent"] : "AI Agent";
    const task = currentTask ? ` — #${currentTask.number}: ${currentTask.title}` : "";
    label = `${who} pracuje${task}`;
    tone = "blue";
  }
  const stale = Object.values(liveness).includes("stale");

  return (
    <div className="flex flex-shrink-0 items-center justify-between gap-2 border-b border-[var(--color-border-default)] px-4 py-1.5 text-xs">
      <span className="flex items-center gap-1.5 text-[var(--color-text-muted)]">
        Na rade:
        <span className={`font-medium ${TONE_TEXT[tone]}`}>{label}</span>
      </span>
      <span className="flex items-center gap-2">
        {drifted && (
          <span
            className="text-[10px] font-medium text-[var(--color-state-warning-fg)]"
            title="Overenie je zastarané: kód sa pohol za overený commit (HEAD sa zmenil). Táto verzia NIE je overená voči aktuálnemu kódu — over ju znova."
          >
            ⚠ overenie zastarané (kód sa pohol)
          </span>
        )}
        {stale && (
          <span className="text-[10px] text-[var(--color-state-warning-fg)]" title="Session bez aktivity > 30 min">
            ⚠ session nečinná
          </span>
        )}
      </span>
    </div>
  );
}

export default PipelineRail;
