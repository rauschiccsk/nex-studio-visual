// Left rail: stage progress + agent status chips (F-007 §7).

import type {
  PipelineActor,
  PipelineStage,
  PipelineState,
} from "../../services/api/pipeline";

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

const STAGE_LABEL: Record<PipelineStage, string> = {
  kickoff: "Kickoff",
  gate_a: "Gate A",
  gate_b: "Gate B",
  gate_c: "Gate C",
  gate_d: "Gate D",
  gate_e: "Gate E",
  build: "Build",
  gate_g: "Gate G",
  release: "Release",
  done: "Done",
};

// Agent chips (director is the human, not an agent chip).
const AGENTS: { actor: PipelineActor; label: string; emoji: string }[] = [
  { actor: "coordinator", label: "Koordinátor", emoji: "🧭" },
  { actor: "designer", label: "Designer", emoji: "🎨" },
  { actor: "customer", label: "Customer", emoji: "🧑‍💼" },
  { actor: "implementer", label: "Implementer", emoji: "🔨" },
  { actor: "auditor", label: "Auditor", emoji: "🔍" },
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
                <span>{STAGE_LABEL[stage]}</span>
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
          {AGENTS.map(({ actor, label, emoji }) => {
            const s = chipStatusFor(actor, state);
            return (
              <li key={actor} className="flex items-center justify-between gap-2 text-xs">
                <span className="flex items-center gap-1.5 text-slate-400">
                  <span aria-hidden="true">{emoji}</span>
                  {label}
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
