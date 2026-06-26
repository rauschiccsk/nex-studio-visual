// Left rail: stage progress + agent status chips (F-007 §7).

import type {
  ActivityLine,
  AgentSession,
  PipelineActor,
  PipelineBoard,
  PipelineState,
} from "../../services/api/pipeline";
import type { StatusTone } from "./labels";
import { FLOW_LABELS, ROLE_LABELS, STAGE_CODES, STAGE_LABELS, stageOrderForFlow, TONE_TEXT } from "./labels";

// The agent actually active (CR-NS-018): while working = the real streaming role
// (latest activity frame, fallback the stage actor); at rest = who just acted
// (latest non-director/system message author). NOT the nominal current_actor.
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

// Agent chips (manazer is the human operator, not an agent chip). Labels from the shared
// ROLE_LABELS map; emoji is decorative.
const AGENTS: { actor: PipelineActor; emoji: string }[] = [
  { actor: "coordinator", emoji: "🧭" },
  { actor: "designer", emoji: "🎨" },
  { actor: "customer", emoji: "🧑‍💼" },
  { actor: "implementer", emoji: "🔨" },
  { actor: "auditor", emoji: "🔍" },
];

// R4 (D5): "stale" = a session untouched > 30 min (from agent_sessions), surfaced on an otherwise-idle chip.
type ChipStatus = "idle" | "working" | "awaiting" | "blocked" | "stale";

// Chip colour from the unified palette (CR-NS-028): working=blue, awaiting=amber, blocked=red,
// idle=neutral — never emerald-for-working. stale=amber (an idle thread needing attention).
const CHIP_TONE: Record<ChipStatus, StatusTone> = {
  idle: "neutral",
  working: "blue",
  awaiting: "amber",
  blocked: "red",
  stale: "amber",
};

const CHIP_LABEL: Record<ChipStatus, string> = {
  idle: "idle",
  working: "working",
  awaiting: "awaiting",
  blocked: "blocked",
  stale: "stale",
};

function chipStatusFor(
  actor: PipelineActor,
  state: PipelineState | null,
  activeAgent: PipelineActor | null,
): ChipStatus {
  // The active agent is the one actually working / who just acted (CR-NS-018) — not
  // the nominal stage actor (at gate_e that's always "customer"). Derived upstream.
  if (!state || activeAgent !== actor) return "idle";
  switch (state.status) {
    case "agent_working":
      return "working";
    case "awaiting_manazer":
      return "awaiting";
    case "blocked":
      return "blocked";
    default:
      return "idle";
  }
}

interface Props {
  state: PipelineState | null;
  /** The agent actually active (working role, or latest message author at rest) —
   *  derived in CockpitPage from activity + messages. Falls back to current_actor. */
  activeAgent?: PipelineActor | null;
  /** R4 (D5): per-role liveness from the board (idle/active/stale). An otherwise-idle chip whose session is
   *  `stale` (untouched > 30 min) shows a "stale" indicator. Absent on an older board → no indicator. */
  agentSessions?: AgentSession[];
}

export function PipelineRail({ state, activeAgent = null, agentSessions }: Props) {
  // R4 (D5): role → liveness lookup for the staleness chip.
  const liveness: Partial<Record<PipelineActor, AgentSession["status"]>> = {};
  for (const s of agentSessions ?? []) liveness[s.role] = s.status;
  // Flow-aware stage path (F-009): a fast_fix pipeline shows only the short lane
  // (kickoff → build → release → done), never the full 11-stage waterfall rail.
  const stageOrder = stageOrderForFlow(state?.flow_type);
  const currentIdx = state ? stageOrder.indexOf(state.current_stage) : -1;
  const flowBadge = state?.flow_type === "fast_fix" ? FLOW_LABELS.fast_fix : null;

  return (
    <div className="flex h-full flex-col gap-5 overflow-y-auto p-4">
      <section>
        <h3 className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
          Pipeline
          {flowBadge && (
            <span className="rounded-full border border-indigo-500/30 bg-indigo-500/20 px-1.5 py-0.5 text-[9px] font-medium normal-case text-[var(--color-accent-primary)]">
              {flowBadge}
            </span>
          )}
        </h3>
        <ul className="space-y-1">
          {stageOrder.map((stage, idx) => {
            // A stage is completed (✓) when it's BEFORE the current stage, OR it IS the current stage
            // and the pipeline has finished (status "done") — so the terminal "Hotovo" shows ✓, not the
            // in-progress ">" marker, when the run is done (CR-NS-099).
            const finished = state?.status === "done";
            const completed = currentIdx >= 0 && (idx < currentIdx || (idx === currentIdx && finished));
            const current = idx === currentIdx && !finished;
            const marker = completed ? "✓" : current ? ">" : "·";
            const color = completed
              ? "text-[var(--color-status-success)]"
              : current
                ? "text-primary-400 font-semibold"
                : "text-[var(--color-text-muted)]";
            return (
              <li key={stage} className={`flex items-center gap-2 text-xs ${color}`}>
                <span className="w-3 text-center font-mono">{marker}</span>
                <span title={STAGE_CODES[stage]}>{STAGE_LABELS[stage]}</span>
                {current && state?.is_regate && (
                  <span className="rounded bg-[var(--color-state-warning-bg)] px-1 text-[9px] text-[var(--color-state-warning-fg)]">
                    re-gate #{state.iteration}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
        {/* R4 (D6): a one-line legend for the stage markers — the ✓/>/· render but had no key. */}
        <p className="mt-2 text-[9px] leading-tight text-[var(--color-text-muted)]">
          <span className="font-mono text-[var(--color-status-success)]">✓</span> hotovo ·{" "}
          <span className="font-mono text-primary-400">{">"}</span> práve ·{" "}
          <span className="font-mono">·</span> ešte neprešlo
        </p>
      </section>

      <section>
        <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
          Agenti
        </h3>
        <ul className="space-y-1.5">
          {AGENTS.map(({ actor, emoji }) => {
            const base = chipStatusFor(actor, state, activeAgent);
            // R4 (D5): an otherwise-idle agent whose session is stale (untouched > 30 min) shows "stale".
            const s: ChipStatus = base === "idle" && liveness[actor] === "stale" ? "stale" : base;
            return (
              <li key={actor} className="flex items-center justify-between gap-2 text-xs">
                <span className="flex items-center gap-1.5 text-[var(--color-text-secondary)]">
                  <span aria-hidden="true">{emoji}</span>
                  {ROLE_LABELS[actor]}
                </span>
                <span className={`font-mono text-[10px] ${TONE_TEXT[CHIP_TONE[s]]}`}>{CHIP_LABEL[s]}</span>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

export default PipelineRail;
