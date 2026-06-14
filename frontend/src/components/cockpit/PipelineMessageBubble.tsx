// Display-only message bubble for the orchestration cockpit (F-007 §7).
//
// The cockpit's own message bubble. The legacy Gate-E DialogueMessageBubble + the standalone
// /dialogue page were retired in CR-NS-065 — Gate E now runs per-question inside the cockpit.

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { PipelineMessage, PipelineParticipant } from "../../services/api/pipeline";
import { ROLE_LABELS, SYNTHESIS_LABEL, RAW_REPORT_LABEL, AUTONOMOUS_LABEL } from "./labels";

const PARTICIPANT_EMOJI: Record<PipelineParticipant, string> = {
  coordinator: "🧭",
  designer: "🎨",
  customer: "🧑‍💼",
  implementer: "🔨",
  auditor: "🔍",
  director: "👔",
  system: "⚙️",
};

const PARTICIPANT_ACCENT: Record<PipelineParticipant, string> = {
  coordinator: "border-sky-500/60 bg-sky-500/5",
  designer: "border-fuchsia-500/60 bg-fuchsia-500/5",
  customer: "border-amber-500/60 bg-amber-500/5",
  implementer: "border-emerald-500/60 bg-emerald-500/5",
  auditor: "border-violet-500/60 bg-violet-500/5",
  director: "border-primary-500/60 bg-primary-500/5",
  system: "border-slate-600/60 bg-slate-700/10",
};

const KIND_BADGE: Record<string, string> = {
  question: "bg-amber-500/15 text-amber-300",
  answer: "bg-sky-500/15 text-sky-300",
  gate_report: "bg-violet-500/15 text-violet-300",
  approval: "bg-emerald-500/15 text-emerald-300",
  return: "bg-red-500/15 text-red-300",
  verdict: "bg-primary-500/15 text-primary-300",
  notification: "bg-slate-600/20 text-slate-300",
  directive: "bg-slate-600/20 text-slate-300",
  kickoff: "bg-slate-600/20 text-slate-300",
};

interface Props {
  message: PipelineMessage;
}

export function PipelineMessageBubble({ message }: Props) {
  const ts = new Date(message.created_at).toLocaleTimeString("sk-SK", {
    hour: "2-digit",
    minute: "2-digit",
  });

  // CR-NS-053 Pillar A (§A.3): the Coordinator's synthesis (payload.is_synthesis) is the PRIMARY
  // Director-facing message — rendered prominently. The raw worker gate_report it summarizes stays in
  // the thread but as a SECONDARY, dimmed "pôvodný report" (drill-down; never removed).
  const isSynthesis = Boolean((message.payload as { is_synthesis?: boolean } | null)?.is_synthesis);
  // CR-NS-055 Pillar B (§B.3): an AUTONOMOUS Coordinator decision (payload.is_autonomous) — the Director SEES
  // every auto-executed bounded recovery (never silent), rendered distinctly ("Koordinátor rozhodol").
  const isAutonomous = Boolean((message.payload as { is_autonomous?: boolean } | null)?.is_autonomous);
  const isRawReport = message.kind === "gate_report" && message.author !== "coordinator" && !isSynthesis;

  const badge = KIND_BADGE[message.kind] ?? "bg-slate-600/20 text-slate-300";
  // Synthesis → prominent primary rail; autonomous decision → amber attention rail; else the per-author
  // accent (dimmed for a raw report).
  const container = isSynthesis
    ? "rounded-r-lg border-l-[6px] border-primary-500 bg-primary-500/10 ring-1 ring-primary-500/20 px-3 py-2.5 text-sm"
    : isAutonomous
      ? "rounded-r-lg border-l-[6px] border-amber-500 bg-amber-500/10 px-3 py-2.5 text-sm"
      : `rounded-r-lg border-l-4 ${PARTICIPANT_ACCENT[message.author] ?? PARTICIPANT_ACCENT.system} px-3 py-2 text-sm${
          isRawReport ? " opacity-60" : ""
        }`;

  return (
    <div className={container}>
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs text-slate-300">
          <span aria-hidden="true">{PARTICIPANT_EMOJI[message.author]}</span>
          <span className="font-semibold">{ROLE_LABELS[message.author]}</span>
          <span className="text-slate-600">→</span>
          <span className="text-slate-400">{ROLE_LABELS[message.recipient]}</span>
          <span className="text-slate-600">·</span>
          <span className="font-mono text-[10px] text-slate-500">{ts}</span>
        </div>
        {isAutonomous ? (
          <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold bg-amber-500/20 text-amber-200">
            {AUTONOMOUS_LABEL}
          </span>
        ) : isSynthesis ? (
          <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold bg-primary-500/20 text-primary-200">
            {SYNTHESIS_LABEL}
          </span>
        ) : (
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${badge}`}>
            {isRawReport ? RAW_REPORT_LABEL : message.kind}
          </span>
        )}
      </div>
      <div
        className="prose prose-sm prose-invert max-w-none leading-relaxed text-slate-200
                   prose-headings:mt-3 prose-headings:font-semibold prose-headings:text-slate-100
                   prose-p:my-1.5 prose-p:text-sm
                   prose-code:bg-slate-800/60 prose-code:px-1 prose-code:text-fuchsia-300
                   prose-pre:bg-slate-900/80"
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
      </div>
    </div>
  );
}

export default PipelineMessageBubble;
