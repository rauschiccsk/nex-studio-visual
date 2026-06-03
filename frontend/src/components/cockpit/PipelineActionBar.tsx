// Context-aware Director action buttons (F-007 §8).
//
// Buttons are derived from current_stage + status. Actions needing free text
// (return/ask/answer) open an inline composer; verdict offers PASS/FAIL.

import { useState } from "react";
import { Loader2 } from "lucide-react";

import type {
  PipelineActionName,
  PipelineState,
} from "../../services/api/pipeline";

const GATE_STAGES = new Set(["gate_a", "gate_b", "gate_c", "gate_d", "gate_e"]);

interface Props {
  state: PipelineState | null;
  inFlight: boolean;
  onAction: (action: PipelineActionName, payload?: Record<string, unknown>) => void;
}

type Composer = { action: PipelineActionName; label: string; field: string } | null;

export function PipelineActionBar({ state, inFlight, onAction }: Props) {
  const [composer, setComposer] = useState<Composer>(null);
  const [text, setText] = useState("");

  if (!state) return null;

  const { current_stage, status } = state;
  const awaiting = status === "awaiting_director";
  const blocked = status === "blocked";
  const working = status === "agent_working";
  const isDone = status === "done";

  const openComposer = (c: NonNullable<Composer>) => {
    setComposer(c);
    setText("");
  };
  const submitComposer = () => {
    if (!composer || !text.trim()) return;
    onAction(composer.action, { [composer.field]: text.trim() });
    setComposer(null);
    setText("");
  };

  const btn =
    "inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium disabled:opacity-50";

  if (composer) {
    return (
      <div className="flex flex-col gap-2">
        <textarea
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={composer.label}
          rows={3}
          className="w-full resize-none rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200 focus:border-primary-500 focus:outline-none"
        />
        <div className="flex items-center gap-2">
          <button
            onClick={submitComposer}
            disabled={inFlight || !text.trim()}
            className={`${btn} bg-primary-600 text-white hover:bg-primary-500`}
          >
            {inFlight ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
            {composer.label}
          </button>
          <button
            onClick={() => setComposer(null)}
            disabled={inFlight}
            className={`${btn} border border-slate-700 text-slate-400 hover:text-slate-200`}
          >
            Zrušiť
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {inFlight && <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-500" />}

      {current_stage === "kickoff" && awaiting && (
        <button
          onClick={() => onAction("start")}
          disabled={inFlight}
          className={`${btn} bg-primary-600 text-white hover:bg-primary-500`}
        >
          Spustiť
        </button>
      )}

      {GATE_STAGES.has(current_stage) && awaiting && (
        <>
          <button
            onClick={() => onAction("approve")}
            disabled={inFlight}
            className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
          >
            Schváliť
          </button>
          <button
            onClick={() => openComposer({ action: "return", label: "Vrátiť s komentárom", field: "comment" })}
            disabled={inFlight}
            className={`${btn} border border-red-500/40 text-red-300 hover:bg-red-500/10`}
          >
            Vrátiť
          </button>
        </>
      )}

      {current_stage === "gate_g" && awaiting && (
        <>
          <button
            onClick={() => onAction("verdict", { verdict: "PASS" })}
            disabled={inFlight}
            className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
          >
            Verdikt PASS
          </button>
          <button
            onClick={() => onAction("verdict", { verdict: "FAIL" })}
            disabled={inFlight}
            className={`${btn} bg-red-600 text-white hover:bg-red-500`}
          >
            Verdikt FAIL
          </button>
        </>
      )}

      {current_stage === "release" && awaiting && (
        <button
          onClick={() => onAction("uat_accept")}
          disabled={inFlight}
          className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
        >
          UAT accept
        </button>
      )}

      {blocked && (
        <button
          onClick={() => openComposer({ action: "answer", label: "Odpovedať agentovi", field: "text" })}
          disabled={inFlight}
          className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}
        >
          Odpoveď
        </button>
      )}

      {working && (
        <button
          onClick={() => onAction("pause")}
          disabled={inFlight}
          className={`${btn} border border-slate-700 text-slate-300 hover:bg-slate-800`}
        >
          Pauza
        </button>
      )}

      {!isDone && (
        <button
          onClick={() => openComposer({ action: "ask", label: "Položiť otázku", field: "text" })}
          disabled={inFlight}
          className={`${btn} border border-slate-700 text-slate-300 hover:bg-slate-800`}
        >
          Otázka
        </button>
      )}
    </div>
  );
}

export default PipelineActionBar;
