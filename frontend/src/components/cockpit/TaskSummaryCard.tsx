// Per-task summary card for the cockpit thread (CR-NS-054 Pillar C, §C.3).
//
// Renders an `is_task_summary` system message as a compact, collapsible card — NEX Command parity:
// task # + title + status + attempt count (always visible); expand → čo urobené + audit verdikt +
// per-attempt error drill-down. Factual surfacing of the payload's `task_summary` (no analysis here —
// the Coordinator's analysis is the Pillar A synthesis at decision points).

import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { PipelineMessage } from "../../services/api/pipeline";
import { TONE_DOT } from "./labels";

interface AuditVerdict {
  task_pass?: boolean | null;
  findings?: string[];
  note?: string;
}

interface TaskSummary {
  task_id: string;
  task_number: number;
  title: string;
  final_status: "done" | "failed";
  attempts: number;
  audit_verdict: AuditVerdict;
  last_error: string | null;
  work_summary: string | null;
  attempt_errors: string[];
}

interface Props {
  message: PipelineMessage;
}

function pokusy(n: number): string {
  if (n === 1) return "1 pokus";
  if (n >= 2 && n <= 4) return `${n} pokusy`;
  return `${n} pokusov`;
}

export function TaskSummaryCard({ message }: Props) {
  const [expanded, setExpanded] = useState(false);
  const ts = (message.payload as { task_summary?: TaskSummary } | null)?.task_summary;
  if (!ts) return null; // defensive — should not happen for an is_task_summary message

  const done = ts.final_status === "done";
  const verdict = ts.audit_verdict ?? {};
  const failedAttempts = ts.attempt_errors ?? [];

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/60">
      {/* Compact header (always visible) */}
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span className={`h-2 w-2 shrink-0 rounded-full ${TONE_DOT[done ? "green" : "red"]}`} aria-hidden="true" />
        <span className="shrink-0 text-xs font-semibold text-slate-200">#{ts.task_number}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-slate-300">{ts.title}</span>
        <span
          className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${
            done ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"
          }`}
        >
          {done ? "hotovo" : "zlyhalo"} · {pokusy(ts.attempts)}
        </span>
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-500" />
        ) : (
          <ChevronUp className="h-3.5 w-3.5 shrink-0 text-slate-500" />
        )}
      </button>

      {expanded && (
        <div className="space-y-3 border-t border-slate-800 px-3 py-2 text-xs">
          {/* (a) čo urobené — the Implementer's final report summary */}
          {ts.work_summary && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500">Čo urobené</div>
              <div className="prose prose-sm prose-invert max-w-none text-slate-200 prose-p:my-1">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{ts.work_summary}</ReactMarkdown>
              </div>
            </div>
          )}

          {/* (b) review / audit verdikt */}
          <div>
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500">Audit</div>
            {verdict.note ? (
              <div className="text-slate-400">{verdict.note}</div>
            ) : (
              <div className={verdict.task_pass ? "text-emerald-300" : "text-red-300"}>
                {verdict.task_pass ? "Prešiel" : "Zlyhal"}
                {verdict.findings && verdict.findings.length > 0 && (
                  <ul className="mt-1 list-disc pl-4 text-slate-300">
                    {verdict.findings.map((f, i) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>

          {/* (c) per-pokus drill-down — every failed attempt's verify_reason (failed-only) */}
          {failedAttempts.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                Pokusy ({failedAttempts.length} {failedAttempts.length === 1 ? "zlyhanie" : "zlyhaní"})
              </div>
              <div className="space-y-1">
                {failedAttempts.map((err, i) => (
                  <pre key={i} className="overflow-x-auto rounded bg-slate-950/70 px-2 py-1 text-[10px] text-red-300">
                    {i + 1}. {err}
                  </pre>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default TaskSummaryCard;
