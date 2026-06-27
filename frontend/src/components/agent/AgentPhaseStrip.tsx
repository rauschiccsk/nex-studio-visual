// AgentPhaseStrip — the thin 4-phase strip on the AI Agent tab (CR-V2-022, design §4.4.1).
//
// "A compact mirror of the Vývoj phase bar (→ links to 🔄 Vývoj)." It shows the build position (●) across the
// four phases without the full tab/click behaviour of the Vývoj PipelineRail — clicking the strip navigates
// to the Vývoj board (the manager view). Read-only, derived from the live pipeline state.

import { useNavigate } from "react-router-dom";
import { ArrowRight } from "lucide-react";

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
  if (buildIdx < 0 || phaseIdx < 0) return "pending";
  if (phaseIdx < buildIdx || (phaseIdx === buildIdx && finished)) return "done";
  if (phaseIdx === buildIdx) return "current";
  return "pending";
}

interface Props {
  state: PipelineState | null;
}

export function AgentPhaseStrip({ state }: Props) {
  const navigate = useNavigate();
  return (
    <button
      onClick={() => navigate("/vyvoj")}
      title="Otvoriť Vývoj"
      className="flex w-full flex-shrink-0 items-center gap-1 overflow-x-auto border-b border-[var(--color-border-default)] px-4 py-1.5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
    >
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
      <ArrowRight className="ml-auto h-3 w-3 flex-shrink-0 text-[var(--color-text-muted)]" />
    </button>
  );
}

export default AgentPhaseStrip;
