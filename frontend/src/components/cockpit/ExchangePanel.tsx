// Right panel: next_action banner + message thread + action bar (F-007 §7).

import { useEffect, useRef } from "react";

import type {
  ActivityLine,
  PipelineActionName,
  PipelineBoard,
  PipelineState,
  PipelineStatus,
} from "../../services/api/pipeline";
import PipelineActionBar from "./PipelineActionBar";
import PipelineActivityFeed from "./PipelineActivityFeed";
import PipelineMessageBubble from "./PipelineMessageBubble";
import { ROLE_LABELS, STAGE_LABELS } from "./labels";

const STATUS_BANNER: Record<PipelineStatus, string> = {
  awaiting_director: "border-amber-500/40 bg-amber-500/10 text-amber-200",
  blocked: "border-red-500/40 bg-red-500/10 text-red-200",
  agent_working: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
  done: "border-slate-600/40 bg-slate-700/10 text-slate-300",
};

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

  const banner = state ? STATUS_BANNER[state.status] : "";
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
  // Gate E boundary signals from the latest Customer gate_report (CR-NS-018 Phase 3):
  // distinguishes a topic boundary (continue) from the final boundary (→ Build), and
  // the open-finding gate that blocks closing.
  const lastCustomerReport = [...recent_messages]
    .reverse()
    .find((m) => m.author === "customer" && m.stage === "gate_e" && m.kind === "gate_report");
  const gateECoverageComplete = lastCustomerReport?.payload?.coverage_complete === true;
  const gateEOpenFindings = Array.isArray(lastCustomerReport?.payload?.findings)
    ? (lastCustomerReport.payload.findings as string[]).length
    : 0;

  return (
    <div className="flex h-full flex-col">
      {state && (
        <div className={`flex-shrink-0 border-b px-4 py-2.5 text-xs ${banner}`}>
          <span className="font-medium text-slate-100">{bannerText(state, isErrorBlock)}</span>
        </div>
      )}

      {state?.status === "agent_working" && (
        <div className="flex-shrink-0">
          <PipelineActivityFeed activity={activity} />
        </div>
      )}

      <div ref={threadRef} className="flex-1 space-y-2 overflow-y-auto p-4">
        {recent_messages.length === 0 ? (
          <div className="py-8 text-center text-xs text-slate-500">
            Zatiaľ žiadne správy v pipeline.
          </div>
        ) : (
          recent_messages.map((m) => <PipelineMessageBubble key={m.id} message={m} />)
        )}
      </div>

      <div className="flex-shrink-0 border-t border-slate-800 p-3">
        <PipelineActionBar
          state={state}
          inFlight={inFlight}
          isErrorBlock={isErrorBlock}
          hasCoordinatorReport={hasCoordinatorReport}
          gateECoverageComplete={gateECoverageComplete}
          gateEOpenFindings={gateEOpenFindings}
          onAction={onAction}
        />
      </div>
    </div>
  );
}

export default ExchangePanel;
