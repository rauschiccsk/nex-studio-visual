// Right panel: next_action banner + message thread + action bar (F-007 §7).

import { useEffect, useRef } from "react";

import type {
  ActivityLine,
  CoordinatorDirective,
  PipelineActionName,
  PipelineBoard,
  PipelineState,
} from "../../services/api/pipeline";
import PipelineActionBar from "./PipelineActionBar";
import PipelineActivityFeed from "./PipelineActivityFeed";
import PipelineMessageBubble from "./PipelineMessageBubble";
import TaskSummaryCard from "./TaskSummaryCard";
import WhosTurnBoard from "./WhosTurnBoard";
import { PIPELINE_STATUS_TONE, ROLE_LABELS, STAGE_LABELS, TONE_BANNER } from "./labels";

// The Coordinator actions the orchestrator can EXECUTE on approval (F-008 §9) — used to decide whether
// the latest directive is an executable proposal (drives the build approve button) vs a plain relay.
const EXECUTABLE_COORDINATOR_ACTIONS = new Set([
  "coordinator_reset_task",
  "coordinator_move_baseline",
  "coordinator_clear_session",
  "coordinator_escalate_dedo",
  "coordinator_route_to_designer",
  // v0.7.0 R2 (D5): the proof-case fix — `capture_backlog_item` is in the BE _EXECUTABLE_COORDINATOR_ACTIONS
  // but had drifted out of this Set (flagged by 3/4 auditors). The R2-c parity contract-test now guards it.
  "capture_backlog_item",
  // CR-NS-103 (F-009 §4.6): the Coordinator's autonomous answer to a routine fast_fix build question —
  // executable (mirrors the BE _EXECUTABLE_COORDINATOR_ACTIONS) so a Director-approved answer is recognized.
  "coordinator_answer_question",
]);

// Compose the banner from machine values + Slovak display labels — never render
// the raw backend ``next_action`` (it embeds machine tokens like 'coordinator').
function bannerText(state: PipelineState, errorBlock: boolean): string {
  const role = ROLE_LABELS[state.current_actor];
  const stage = STAGE_LABELS[state.current_stage];
  switch (state.status) {
    case "agent_working":
      return `${role} pracuje na fáze ${stage}`;
    case "awaiting_director":
      return `Na rade: Director — posúď fázu ${stage}`;
    case "blocked":
      return errorBlock
        ? `Agent zlyhal vo fáze ${stage} — skús znova`
        : `Na rade: Director — odpovedz ${role}-ovi`;
    case "paused":
      return "Build pozastavený — pokračuj alebo ukonči";
    case "done":
      return "Hotovo";
    default:
      return stage;
  }
}

interface Props {
  board: PipelineBoard;
  inFlight: boolean;
  activity: ActivityLine[];
  onAction: (action: PipelineActionName, payload?: Record<string, unknown>) => void;
}

export function ExchangePanel({ board, inFlight, activity, onAction }: Props) {
  const { state, recent_messages } = board;
  const threadRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    threadRef.current?.scrollTo?.({ top: threadRef.current.scrollHeight });
  }, [recent_messages.length]);

  // Banner tone from the unified palette (CR-NS-028): agent_working=blue, awaiting=amber, blocked=red,
  // done=green — never emerald-for-working.
  const banner = state ? TONE_BANNER[PIPELINE_STATUS_TONE[state.status] ?? "neutral"] : "";
  // An error-block (agent crash/timeout) escalates via a system notification —
  // its last message is authored by "system" (an agent question is authored by
  // the agent role). Drives the "Skús znova" retry vs answer/approve choice.
  const lastMessage = recent_messages[recent_messages.length - 1];
  const isErrorBlock = state?.status === "blocked" && lastMessage?.author === "system";
  // Drives the "Schváliť návrh Koordinátora" button: only offer it when there is
  // a Coordinator gate_report to apply (else the action would 400). CR-NS-018.
  const hasCoordinatorReport = recent_messages.some(
    (m) => m.author === "coordinator" && m.kind === "gate_report",
  );
  // E7 (F-008 §9): the latest EXECUTABLE Coordinator proposal (the Director approves it via
  // apply_coordinator_recommendation → the orchestrator executes the matching action). Only an
  // executable directive (not a relay / director_decision / low-confidence) drives the build approve
  // button + its effect label; a non-executable one stays a plain relay (the usual decision set).
  const latestCoordinatorDirective = [...recent_messages]
    .reverse()
    .map((m) => (m.payload as { coordinator_directive?: CoordinatorDirective } | null)?.coordinator_directive)
    .find((d): d is CoordinatorDirective => Boolean(d));
  const coordinatorProposal =
    latestCoordinatorDirective &&
    latestCoordinatorDirective.triage_class !== "director_decision" &&
    latestCoordinatorDirective.confidence >= 0.8 &&
    EXECUTABLE_COORDINATOR_ACTIONS.has(latestCoordinatorDirective.proposed_action)
      ? latestCoordinatorDirective
      : null;
  // Gate E boundary signals from the latest Customer gate_report (CR-NS-018 Phase 3):
  // distinguishes a topic boundary (continue) from the final boundary (→ Build), and
  // the open-finding gate that blocks closing.
  const lastCustomerReport = [...recent_messages]
    .reverse()
    .find((m) => m.author === "customer" && m.stage === "gate_e" && m.kind === "gate_report");
  const gateECoverageComplete = lastCustomerReport?.payload?.coverage_complete === true;
  // Deterministic open-finding count from the board (CR-NS-018 §5) — NOT the Customer's
  // self-reported findings array (which an imprecise summary could wrongly inflate).
  const gateEOpenFindings = board.gate_e_open_findings ?? 0;
  // Per-question stop vs topic boundary (revised §2): the latest gate_e milestone is
  // either a Designer answer (per-question — Branch A/B) or a Customer gate_report
  // (topic boundary). gap_found on that answer → Branch B (Opraviť/Ponechať).
  const lastGateEMilestone = [...recent_messages]
    .reverse()
    .find(
      (m) =>
        m.stage === "gate_e" &&
        ((m.author === "designer" && m.kind === "answer") ||
          (m.author === "customer" && m.kind === "gate_report")),
    );
  const gateEMode = !lastGateEMilestone
    ? null
    : lastGateEMilestone.author === "customer"
      ? "boundary"
      : "question";
  const gateEGap = lastGateEMilestone?.author === "designer" && lastGateEMilestone.payload?.gap_found === true;

  return (
    <div className="flex h-full flex-col">
      {state && (
        <div className={`flex-shrink-0 border-b px-4 py-2.5 text-xs ${banner}`}>
          <span className="font-medium text-[var(--color-text-primary)]">{bannerText(state, isErrorBlock)}</span>
        </div>
      )}

      {/* "Kto je na rade" board (WS-C2, CR-NS-035): whose turn + decision-type + relay chain + current
          task + the Coordinator's proposed action — honest, derived from the live state. Not at `done`
          (the pipeline is finished — no one's turn; the banner's "Hotovo" suffices). */}
      {state && state.status !== "done" && (
        <WhosTurnBoard
          state={state}
          availableActions={board.available_actions}
          currentTask={board.current_task}
          coordinatorProposal={coordinatorProposal}
          regateProposal={board.regate_proposal}
        />
      )}

      <div ref={threadRef} className="flex-1 space-y-2 overflow-y-auto p-4">
        {recent_messages.length === 0 ? (
          <div className="py-8 text-center text-xs text-[var(--color-text-muted)]">
            Zatiaľ žiadne správy v pipeline.
          </div>
        ) : (
          recent_messages.map((m) =>
            (m.payload as { is_task_summary?: boolean } | null)?.is_task_summary ? (
              <TaskSummaryCard key={m.id} message={m} />
            ) : (
              <PipelineMessageBubble key={m.id} message={m} />
            ),
          )
        )}
      </div>

      {/* Live activity feed BELOW the thread (CR-NS-026): flow reads top-to-bottom banner → thread →
          live activity → action bar, so the streaming action sits right above the controls. */}
      {state?.status === "agent_working" && (
        <div className="flex-shrink-0">
          <PipelineActivityFeed activity={activity} />
        </div>
      )}

      <div className="flex-shrink-0 border-t border-[var(--color-border-default)] p-3">
        <PipelineActionBar
          state={state}
          availableActions={board.available_actions}
          allTasksDone={board.all_tasks_done}
          buildOpenFindings={board.build_open_findings}
          coordinatorProposal={coordinatorProposal}
          regateProposal={board.regate_proposal}
          inFlight={inFlight}
          isErrorBlock={isErrorBlock}
          hasCoordinatorReport={hasCoordinatorReport}
          gateECoverageComplete={gateECoverageComplete}
          gateEOpenFindings={gateEOpenFindings}
          gateEMode={gateEMode}
          gateEGap={gateEGap}
          onAction={onAction}
        />
      </div>
    </div>
  );
}

export default ExchangePanel;
