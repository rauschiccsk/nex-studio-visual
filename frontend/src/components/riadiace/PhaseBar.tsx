// PhaseBar — the read-only phase bar across the top of the Riadiace centrum (spine STEP 1). Salvaged from the
// proven (now-CUT) agent phase strip, MINUS the navigate-to-/vyvoj behaviour — this bar IS on
// the spine page, so it is a read-only marker, not a link.
//
// It marks the build position (●) across the phases, derived live from the pipeline board. There are TWO
// derivations:
//   - LEGACY (phase automaton): the four v2 phases Príprava › Návrh › Programovanie › Verifikácia, marked from
//     the state's `current_stage` index. Tolerates a null / absent / unknown stage (everything neutral, never
//     crash). Rendered BYTE-IDENTICAL to the pre-STEP-5 bar.
//   - CONVERSATION (spine build, STEP 5): a spine build stays on `current_stage='priprava'` end-to-end, so the
//     stage index can't mark it. When `board.state.mode === 'conversation'` the bar instead shows the redesign
//     phases Špecifikácia → Plán → Programovanie → Kontrola, with the current one DERIVED FROM BOARD SIGNALS
//     (available_actions / recent-message payload flags / status), the same honest-by-construction pattern the
//     Plán úloh rail uses for its build triggers.

import type { PipelineActionName, PipelineBoard, PipelineState } from "../../services/api/pipeline";
import type { BuildPhase, StatusTone } from "../cockpit/labels";
import { PHASE_LABELS, PHASE_ORDER, TONE_TEXT } from "../cockpit/labels";

// The four real legacy phases (Hotovo is the terminal sentinel — not shown as a strip step).
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

// ── Conversation (spine) strip — STEP 5 ───────────────────────────────────────
// The spine build never leaves current_stage='priprava', so its position is derived from BOARD SIGNALS, not the
// stage index. Distinct from PHASE_LABELS (those stay the legacy phase-automaton labels, unchanged).
type ConvPhase = "specifikacia" | "plan" | "programovanie" | "kontrola";
const CONV_PHASES: ConvPhase[] = ["specifikacia", "plan", "programovanie", "kontrola"];
const CONV_LABELS: Record<ConvPhase, string> = {
  specifikacia: "Špecifikácia",
  plan: "Plán",
  programovanie: "Programovanie",
  kontrola: "Kontrola",
};

// Which conversation phase is CURRENT, derived from the live board — grounded on the same signals the Plán úloh
// rail reads (available_actions / spec_approved / current_stage / status) plus the durable payload flags carried
// on the recent messages. Evaluated furthest-right first so a later phase wins when several signals overlap.
function conversationPhase(board: PipelineBoard | null): ConvPhase {
  const actions = board?.available_actions ?? [];
  const has = (a: PipelineActionName) => actions.includes(a);
  const msgs = board?.recent_messages ?? [];
  const msgHasFlag = (flag: string) => msgs.some((m) => m.payload?.[flag] === true);
  const working = board?.state?.status === "agent_working";
  const stage = board?.state?.current_stage;

  // Kontrola — the check is offered, has already been reported, or is running right after the build completed.
  if (has("skontrolovat") || msgHasFlag("kontrola") || (working && msgHasFlag("programming_complete"))) {
    return "kontrola";
  }
  // Programovanie — the build is running / paused, or is built-and-ready to start.
  if (has("pause") || has("pokracovat") || has("spustit_stavbu") || stage === "programovanie") {
    return "programovanie";
  }
  // Plán — the Špecifikácia is frozen and no build/check has started yet.
  if (board?.spec_approved) return "plan";
  // Špecifikácia — the default first phase.
  return "specifikacia";
}

function convMarkFor(phase: ConvPhase, current: ConvPhase): PhaseMark {
  const phaseIdx = CONV_PHASES.indexOf(phase);
  const curIdx = CONV_PHASES.indexOf(current);
  if (phaseIdx < curIdx) return "done";
  if (phaseIdx === curIdx) return "current";
  return "pending";
}

interface Props {
  board: PipelineBoard | null;
}

export function PhaseBar({ board }: Props) {
  const state = board?.state ?? null;

  // CONVERSATION (spine) build — the redesign four-phase strip, marked from board signals (STEP 5).
  if (state?.mode === "conversation") {
    const current = conversationPhase(board);
    return (
      <div className="flex w-full flex-shrink-0 items-center gap-1 overflow-x-auto border-b border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-1.5">
        {CONV_PHASES.map((phase, idx) => {
          const mark = convMarkFor(phase, current);
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
                  {CONV_LABELS[phase]}
                </span>
              </span>
            </span>
          );
        })}
      </div>
    );
  }

  // LEGACY (phase automaton) — UNCHANGED, byte-identical to the pre-STEP-5 bar.
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
