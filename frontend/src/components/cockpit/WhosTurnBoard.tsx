// "Kto je na rade" board (WS-C2, CR-NS-035): at a glance — whose turn it is, what decision the Director
// faces, the relay chain (Director → Coordinator → worker), the current build task, and the Coordinator's
// proposed action. Honest: derived from the LIVE state + available_actions, never a stale stage actor.

import type { CoordinatorDirective, PipelineActionName, PipelineState } from "../../services/api/pipeline";
import { COORDINATOR_ACTION_LABELS, ROLE_LABELS, STAGE_LABELS } from "./labels";

// Stages where a Director decision (return/ask) is relayed Director → Coordinator → worker (F-007 §6 / E7).
const RELAYED_STAGES = new Set(["build", "task_plan", "gate_e"]);

// The decision the Director faces, derived from status + available_actions (the WS-C1 single source).
function decisionType(status: string, stage: string, actions: PipelineActionName[]): string | null {
  if (status === "agent_working" || status === "done") return null; // no Director decision
  if (status === "paused") return "Pokračovať alebo ukončiť build";
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
}

export function WhosTurnBoard({ state, availableActions, currentTask, coordinatorProposal }: Props) {
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
    <div className="flex-shrink-0 border-b border-slate-800 px-4 py-2 text-[11px]">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-semibold text-slate-200">{whoseTurn}</span>
        <span className="text-slate-600">·</span>
        <span className="text-slate-400">fáza {stageLabel}</span>
        {decision && (
          <>
            <span className="text-slate-600">·</span>
            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300">{decision}</span>
          </>
        )}
        {currentTask && (
          <>
            <span className="text-slate-600">·</span>
            <span className="text-sky-300">
              úloha #{currentTask.number}: <span className="text-slate-300">{currentTask.title}</span>
            </span>
          </>
        )}
      </div>

      {relayed && (
        <div className="mt-1 flex items-center gap-1 text-[10px] text-slate-500">
          <span>Director</span>
          <span className="text-slate-600">→</span>
          <span className="text-slate-400">Koordinátor</span>
          <span className="text-slate-600">→</span>
          <span>{actorLabel}</span>
          <span className="ml-1 rounded bg-slate-800 px-1 text-[9px] text-slate-500">cez Koordinátora</span>
        </div>
      )}

      {coordinatorProposal && (
        <div className="mt-1 text-[10px] text-indigo-300">
          Návrh Koordinátora:{" "}
          {COORDINATOR_ACTION_LABELS[coordinatorProposal.proposed_action] ?? coordinatorProposal.proposed_action}
          {coordinatorProposal.rationale ? (
            <span className="text-slate-500"> — {coordinatorProposal.rationale}</span>
          ) : null}
        </div>
      )}
    </div>
  );
}

export default WhosTurnBoard;
