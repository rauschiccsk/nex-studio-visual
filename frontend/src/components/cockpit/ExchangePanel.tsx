// Right panel: next_action banner + message thread + action bar (F-007 §7).

import { useEffect, useRef } from "react";

import type {
  PipelineActionName,
  PipelineBoard,
  PipelineStatus,
} from "../../services/api/pipeline";
import PipelineActionBar from "./PipelineActionBar";
import PipelineMessageBubble from "./PipelineMessageBubble";

const STATUS_BANNER: Record<PipelineStatus, string> = {
  awaiting_director: "border-amber-500/40 bg-amber-500/10 text-amber-200",
  blocked: "border-red-500/40 bg-red-500/10 text-red-200",
  agent_working: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
  done: "border-slate-600/40 bg-slate-700/10 text-slate-300",
};

const STATUS_LABEL: Record<PipelineStatus, string> = {
  awaiting_director: "NA RADE: Director",
  blocked: "BLOKOVANÉ: agent sa pýta",
  agent_working: "Agent pracuje…",
  done: "Hotovo",
};

interface Props {
  board: PipelineBoard;
  inFlight: boolean;
  onAction: (action: PipelineActionName, payload?: Record<string, unknown>) => void;
}

export function ExchangePanel({ board, inFlight, onAction }: Props) {
  const { state, recent_messages } = board;
  const threadRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [recent_messages.length]);

  const banner = state ? STATUS_BANNER[state.status] : "";

  return (
    <div className="flex h-full flex-col">
      {state && (
        <div className={`flex-shrink-0 border-b px-4 py-2.5 text-xs ${banner}`}>
          <span className="font-mono">&gt; {STATUS_LABEL[state.status]}</span>
          {state.next_action && <span className="ml-2 text-slate-200">— {state.next_action}</span>}
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
        <PipelineActionBar state={state} inFlight={inFlight} onAction={onAction} />
      </div>
    </div>
  );
}

export default ExchangePanel;
