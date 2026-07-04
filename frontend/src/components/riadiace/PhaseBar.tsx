// PhaseBar — the read-only phase bar across the top of the Riadiace centrum (spine STEP 1). Salvaged from the
// proven components/agent/AgentPhaseStrip.tsx (CUT), MINUS the navigate-to-/vyvoj behaviour — this bar IS on
// the spine page, so it is a read-only marker, not a link.
//
// It marks the build position (●) across the four phases, derived live from the pipeline state. The backend
// phase model is in flux during the spine steps, so the bar MUST tolerate a null / absent / unknown
// current_stage — it renders every phase neutral (pending) rather than crash (phaseMarkFor returns "pending"
// when the state is null or the stage is not in PHASE_ORDER).

import type { PipelineState } from "../../services/api/pipeline";
import type { BuildPhase, StatusTone } from "../cockpit/labels";
import { PHASE_LABELS, PHASE_ORDER, TONE_TEXT } from "../cockpit/labels";

// The four real phases (Hotovo is the terminal sentinel — not shown as a strip step).
const STRIP_PHASES: BuildPhase[] = PHASE_ORDER.filter((p) => p !== "done");

type PhaseMark = "done" | "current" | "pending";
const MARK_GLYPH: Record<PhaseMark, string> = { done: "✓", current: "●", pending: "○" };
const MARK_TONE: Record<PhaseMark, StatusTone> = { done: "green", current: "blue", pending: "neutral" };

function phaseMarkFor(phase: BuildPhase, state: PipelineState | null): PhaseMark {
  if (!state) return "pending";
  const buildIdx = PHASE_ORDER.indexOf(state.current_stage as BuildPhase);
  const phaseIdx = PHASE_ORDER.indexOf(phase);
  const finished = state.status === "done";
  // Unknown / absent stage (buildIdx < 0) → everything neutral (pending), never crash.
  if (buildIdx < 0 || phaseIdx < 0) return "pending";
  if (phaseIdx < buildIdx || (phaseIdx === buildIdx && finished)) return "done";
  if (phaseIdx === buildIdx) return "current";
  return "pending";
}

interface Props {
  state: PipelineState | null;
}

export function PhaseBar({ state }: Props) {
  return (
    <div className="flex w-full flex-shrink-0 items-center gap-1 overflow-x-auto border-b border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-1.5">
      {STRIP_PHASES.map((phase, idx) => {
        const mark = phaseMarkFor(phase, state);
        const tone = MARK_TONE[mark];
        return (
          <span key={phase} className="flex items-center">
            {idx > 0 && <span className="px-1 text-[10px] text-[var(--color-text-muted)]">›</span>}
            <span className="flex items-center gap-1 text-[11px]">
              <span className={`font-mono ${TONE_TEXT[tone]}`} aria-hidden="true">
                {MARK_GLYPH[mark]}
              </span>
              <span
                className={
                  mark === "current"
                    ? "font-semibold text-[var(--color-text-primary)]"
                    : "text-[var(--color-text-secondary)]"
                }
              >
                {PHASE_LABELS[phase]}
              </span>
            </span>
          </span>
        );
      })}
    </div>
  );
}

export default PhaseBar;
