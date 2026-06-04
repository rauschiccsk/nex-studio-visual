// Left rail: stage progress + agent status chips (F-007 §7).

import type {
  PipelineActor,
  PipelineStage,
  PipelineState,
} from "../../services/api/pipeline";
import { ROLE_LABELS, STAGE_CODES, STAGE_LABELS } from "./labels";

// Mirrors backend orchestrator.STAGE_ORDER + STAGE_ACTOR.
const STAGE_ORDER: PipelineStage[] = [
  "kickoff",
  "gate_a",
  "gate_b",
  "gate_c",
  "gate_d",
  "gate_e",
  "build",
  "gate_g",
  "release",
  "done",
];

// Agent chips (director is the human, not an agent chip). Labels from the shared
// ROLE_LABELS map; emoji is decorative.
const AGENTS: { actor: PipelineActor; emoji: string }[] = [
  { actor: "coordinator", emoji: "🧭" },
  { actor: "designer", emoji: "🎨" },
  { actor: "customer", emoji: "🧑‍💼" },
  { actor: "implementer", emoji: "🔨" },
  { actor: "auditor", emoji: "🔍" },
];

type ChipStatus = "idle" | "working" | "awaiting" | "blocked";

const CHIP_STYLE: Record<ChipStatus, string> = {
  idle: "text-slate-600",
  working: "text-emerald-400",
  awaiting: "text-amber-400",
  blocked: "text-red-400",
};

const CHIP_LABEL: Record<ChipStatus, string> = {
  idle: "idle",
  working: "working",
  awaiting: "awaiting",
  blocked: "blocked",
};

function chipStatusFor(actor: PipelineActor, state: PipelineState | null): ChipStatus {
  if (!state || state.current_actor !== actor) return "idle";
  switch (state.status) {
    case "agent_working":
      return "working";
    case "awaiting_director":
      return "awaiting";
    case "blocked":
      return "blocked";
    default:
      return "idle";
  }
}

interface Props {
  state: PipelineState | null;
}

export function PipelineRail({ state }: Props) {
  const currentIdx = state ? STAGE_ORDER.indexOf(state.current_stage) : -1;

  return (
    <div className="flex h-full flex-col gap-5 overflow-y-auto p-4">
      <section>
        <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          Pipeline
        </h3>
        <ul className="space-y-1">
          {STAGE_ORDER.map((stage, idx) => {
            const done = currentIdx >= 0 && idx < currentIdx;
            const current = idx === currentIdx;
            const marker = done ? "✓" : current ? ">" : "·";
            const color = done
              ? "text-emerald-500"
              : current
                ? "text-primary-400 font-semibold"
                : "text-slate-600";
            return (
              <li key={stage} className={`flex items-center gap-2 text-xs ${color}`}>
                <span className="w-3 text-center font-mono">{marker}</span>
                <span title={STAGE_CODES[stage]}>{STAGE_LABELS[stage]}</span>
                {current && state?.is_regate && (
                  <span className="rounded bg-amber-500/15 px-1 text-[9px] text-amber-300">
                    re-gate #{state.iteration}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      <section>
        <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          Agenti
        </h3>
        <ul className="space-y-1.5">
          {AGENTS.map(({ actor, emoji }) => {
            const s = chipStatusFor(actor, state);
            return (
              <li key={actor} className="flex items-center justify-between gap-2 text-xs">
                <span className="flex items-center gap-1.5 text-slate-400">
                  <span aria-hidden="true">{emoji}</span>
                  {ROLE_LABELS[actor]}
                </span>
                <span className={`font-mono text-[10px] ${CHIP_STYLE[s]}`}>{CHIP_LABEL[s]}</span>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

export default PipelineRail;
