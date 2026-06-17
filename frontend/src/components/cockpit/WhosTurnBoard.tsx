// "Kto je na rade" board (WS-C2, CR-NS-035): at a glance — whose turn it is, what decision the Director
// faces, the relay chain (Director → Coordinator → worker), the current build task, and the Coordinator's
// proposed action. Honest: derived from the LIVE state + available_actions, never a stale stage actor.

import type {
  AutonomousDecisionsSummary,
  CoordinatorDirective,
  CoordinatorTriage,
  PipelineActionName,
  PipelineStage,
  PipelineState,
} from "../../services/api/pipeline";
import { COORDINATOR_ACTION_LABELS, ROLE_LABELS, STAGE_LABELS, TRIAGE_CLASS_LABELS } from "./labels";

// Stages where a Director decision (return/ask) is relayed Director → Coordinator → worker (F-007 §6 / E7).
const RELAYED_STAGES = new Set(["build", "task_plan", "gate_e"]);

// The decision the Director faces, derived from status + available_actions (the WS-C1 single source).
function decisionType(status: string, stage: string, actions: PipelineActionName[]): string | null {
  if (status === "agent_working" || status === "done") return null; // no Director decision
  if (status === "paused") return "Pokračovať alebo ukončiť build";
  // CR-NS-056 §F1.7: a gate_g scope escalation reads as answer-or-decide (the action bar offers Odpoveď +
  // Fix-2's FAIL→target). Checked BEFORE the verdict branch (Fix 2 adds verdict to gate_g/blocked).
  if (stage === "gate_g" && status === "blocked") return "Odpovedz alebo rozhodni";
  if (actions.includes("verdict")) return "Verdikt auditu (PASS / FAIL)";
  if (actions.includes("uat_accept")) return "Akceptovať verziu (UAT)";
  if (status === "blocked") return actions.includes("answer") ? "Odpovedať / vrátiť" : "Vrátiť";
  if (stage === "build") return "Schváliť build / pokračovať / vrátiť úlohu";
  return "Schváliť alebo vrátiť"; // ratify gates (kickoff / gate_a-d / task_plan)
}

interface Props {
  state: PipelineState;
  availableActions?: PipelineActionName[];
  currentTask?: { number: number; title: string } | null;
  coordinatorProposal?: CoordinatorDirective | null;
  /** gate_g FAIL re-gate proposal (CR-NS-057 §F2.4) — shown as a one-liner at gate_g awaiting|blocked. */
  regateProposal?: { entry_stage: PipelineStage; reason?: string } | null;
  /** R4 (D3): the latest Coordinator relay/escalation triage — surfaced for a NON-executable relay (the
   *  executable proposal is already shown via coordinatorProposal). Absent/null → render nothing. */
  coordinatorTriage?: CoordinatorTriage | null;
  /** R4 (D4): board roll-up of autonomous Coordinator decisions — the line shows only when count > 0. */
  autonomousSummary?: AutonomousDecisionsSummary | null;
}

export function WhosTurnBoard({
  state,
  availableActions,
  currentTask,
  coordinatorProposal,
  regateProposal,
  coordinatorTriage,
  autonomousSummary,
}: Props) {
  const { current_stage, current_actor, status } = state;
  const actorLabel = ROLE_LABELS[current_actor] ?? current_actor;
  const stageLabel = STAGE_LABELS[current_stage] ?? current_stage;
  const whoseTurn =
    status === "agent_working"
      ? `${actorLabel} pracuje`
      : status === "paused"
        ? "Build pozastavený"
        : status === "done"
          ? "Hotovo"
          : "Na rade: Director";
  const decision = decisionType(status, current_stage, availableActions ?? []);
  const relayed = RELAYED_STAGES.has(current_stage) && status !== "agent_working" && status !== "done";

  return (
    <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-4 py-2 text-[11px]">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-semibold text-[var(--color-text-primary)]">{whoseTurn}</span>
        <span className="text-[var(--color-text-muted)]">·</span>
        <span className="text-[var(--color-text-secondary)]">fáza {stageLabel}</span>
        {decision && (
          <>
            <span className="text-[var(--color-text-muted)]">·</span>
            <span className="rounded bg-[var(--color-surface)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-secondary)]">{decision}</span>
          </>
        )}
        {currentTask && (
          <>
            <span className="text-[var(--color-text-muted)]">·</span>
            <span className="text-[var(--color-status-info)]">
              úloha #{currentTask.number}: <span className="text-[var(--color-text-secondary)]">{currentTask.title}</span>
            </span>
          </>
        )}
      </div>

      {relayed && (
        <div className="mt-1 flex items-center gap-1 text-[10px] text-[var(--color-text-muted)]">
          <span>Director</span>
          <span className="text-[var(--color-text-muted)]">→</span>
          <span className="text-[var(--color-text-secondary)]">Koordinátor</span>
          <span className="text-[var(--color-text-muted)]">→</span>
          <span>{actorLabel}</span>
          <span className="ml-1 rounded bg-[var(--color-surface)] px-1 text-[9px] text-[var(--color-text-muted)]">cez Koordinátora</span>
        </div>
      )}

      {coordinatorProposal && (
        <div className="mt-1 text-[10px] text-[var(--color-accent-primary)]">
          Návrh Koordinátora:{" "}
          {COORDINATOR_ACTION_LABELS[coordinatorProposal.proposed_action] ?? coordinatorProposal.proposed_action}
          {coordinatorProposal.rationale ? (
            <span className="text-[var(--color-text-muted)]"> — {coordinatorProposal.rationale}</span>
          ) : null}
        </div>
      )}

      {/* CR-NS-057 §F2.4: at gate_g (awaiting OR blocked) propose the FAIL re-gate target. */}
      {regateProposal && (STAGE_LABELS[regateProposal.entry_stage] ?? null) && (
        <div className="mt-1 text-[10px] text-[var(--color-accent-primary)]">
          Navrhovaný návrat pri FAIL: {STAGE_LABELS[regateProposal.entry_stage]}
          {regateProposal.reason ? <span className="text-[var(--color-text-muted)]"> — {regateProposal.reason}</span> : null}
        </div>
      )}

      {/* R4 (D3): the Coordinator's triage on a NON-executable relay (director_decision / low-confidence) —
          the executable case is already shown above as "Návrh Koordinátora", so suppress this then to avoid
          a duplicate line. */}
      {!coordinatorProposal && coordinatorTriage?.triage_class && (
        <div className="mt-1 text-[10px] text-[var(--color-text-secondary)]">
          Koordinátor klasifikoval:{" "}
          {TRIAGE_CLASS_LABELS[coordinatorTriage.triage_class] ?? coordinatorTriage.triage_class}
          {typeof coordinatorTriage.confidence === "number"
            ? ` (istota ${Math.round(coordinatorTriage.confidence * 100)} %)`
            : ""}
          {coordinatorTriage.proposed_action
            ? `, navrhuje ${COORDINATOR_ACTION_LABELS[coordinatorTriage.proposed_action] ?? coordinatorTriage.proposed_action}`
            : ""}
        </div>
      )}

      {/* R4 (D4): at-a-glance roll-up of autonomous Coordinator decisions (the per-message amber bubbles stay
          in the thread). Rendered only when there has been at least one. */}
      {autonomousSummary && autonomousSummary.count > 0 && (
        <div className="mt-1 text-[10px] text-[var(--color-text-muted)]">
          Koordinátor rozhodol samostatne {autonomousSummary.count}×
          {autonomousSummary.recent[0]?.rationale ? ` — naposledy: ${autonomousSummary.recent[0].rationale}` : ""}
        </div>
      )}
    </div>
  );
}

export default WhosTurnBoard;
