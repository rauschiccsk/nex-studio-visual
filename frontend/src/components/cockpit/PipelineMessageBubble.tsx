// Display-only message bubble for the orchestration cockpit (F-007 §7).
//
// The cockpit's own message bubble. The legacy Gate-E DialogueMessageBubble + the standalone
// /dialogue page were retired in CR-NS-065 — Gate E now runs per-question inside the cockpit.

import { useState } from "react";
import { Check, ChevronDown, ChevronUp, Copy } from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { CodeBlock } from "../markdown/CodeBlock";
import { useCopyToClipboard } from "../../hooks/useCopyToClipboard";
import type { PipelineMessage, PipelineParticipant } from "../../services/api/pipeline";
import { ROLE_LABELS, SYNTHESIS_LABEL, RAW_REPORT_LABEL, AUTONOMOUS_LABEL, DIRECTOR_BRIEF_LABEL } from "./labels";

const PARTICIPANT_EMOJI: Record<PipelineParticipant, string> = {
  coordinator: "🧭",
  designer: "🎨",
  customer: "🧑‍💼",
  implementer: "🔨",
  auditor: "🔍",
  manazer: "👔",
  system: "⚙️",
};

const PARTICIPANT_ACCENT: Record<PipelineParticipant, string> = {
  coordinator: "border-sky-500/60 bg-sky-500/5",
  designer: "border-fuchsia-500/60 bg-fuchsia-500/5",
  customer: "border-amber-500/60 bg-amber-500/5",
  implementer: "border-emerald-500/60 bg-emerald-500/5",
  auditor: "border-violet-500/60 bg-violet-500/5",
  manazer: "border-primary-500/60 bg-primary-500/5",
  system: "border-[var(--color-border-strong)] bg-[var(--color-surface-hover)]",
};

const KIND_BADGE: Record<string, string> = {
  // CR-NS-067c: light-readable + dark-identical via `text-X-700 dark:text-X-300`
  // (the right-side message-kind labels were unreadable light pastels on white).
  question: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  answer: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  gate_report: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  approval: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  return: "bg-red-500/15 text-red-700 dark:text-red-300",
  verdict: "bg-primary-500/15 text-primary-700 dark:text-primary-300",
  notification: "bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]",
  directive: "bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]",
  kickoff: "bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]",
};

// Shared `prose` styling for the markdown body (used for both the brief body and a plain message).
const PROSE_CLASS =
  "prose prose-sm dark:prose-invert max-w-none leading-relaxed text-[var(--color-text-primary)] " +
  "prose-headings:mt-3 prose-headings:font-semibold prose-headings:text-[var(--color-text-primary)] " +
  "prose-p:my-1.5 prose-p:text-sm " +
  "prose-strong:font-semibold prose-strong:text-[var(--color-text-primary)] " +
  "prose-ul:my-1.5 prose-ul:list-disc prose-ul:pl-5 prose-li:my-0.5 " +
  "prose-code:bg-[var(--color-surface-hover)] prose-code:px-1 prose-code:text-[var(--color-version-text)] " +
  "prose-pre:bg-[var(--color-surface-hover)]";

// Rich-report prose: ordered lists + clean inline code (drop the prose backtick ::before/::after) + a
// flat pre (the fenced-code component below owns the box). Used only for the recovered agent report body.
const REPORT_PROSE_CLASS =
  "prose prose-sm dark:prose-invert max-w-none leading-relaxed text-[var(--color-text-primary)] " +
  "prose-headings:mt-3 prose-headings:mb-1 prose-headings:font-semibold prose-headings:text-[var(--color-text-primary)] " +
  "prose-p:my-1.5 prose-p:text-sm " +
  "prose-strong:font-semibold prose-strong:text-[var(--color-text-primary)] " +
  "prose-ul:my-1.5 prose-ul:list-disc prose-ul:pl-5 prose-ol:my-1.5 prose-ol:list-decimal prose-ol:pl-5 prose-li:my-0.5 " +
  "prose-code:before:content-none prose-code:after:content-none " +
  "prose-pre:bg-transparent prose-pre:p-0 prose-pre:my-0";

// Markdown component overrides for the report body: fenced code → the shared CodeBlock (language label +
// copy button), inline code → a subtle-highlight monospace chip (file paths + identifiers). Mirrors the
// established in-repo idiom (ProjectSpecsPage / KnowledgeBasePage). Module-level so it isn't re-created.
const REPORT_MARKDOWN_COMPONENTS: Components = {
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || "");
    const isInline = !className && typeof children === "string" && !children.includes("\n");
    if (!isInline && match) {
      return <CodeBlock language={match[1]}>{String(children)}</CodeBlock>;
    }
    if (!isInline && typeof children === "string" && children.includes("\n")) {
      return <CodeBlock>{String(children)}</CodeBlock>;
    }
    return (
      <code
        className="rounded bg-[var(--color-surface-hover)] px-1 py-0.5 font-mono text-[0.85em] text-[var(--color-version-text)]"
        {...props}
      >
        {children}
      </code>
    );
  },
  pre({ children }) {
    return <>{children}</>;
  },
};

// Beyond this many lines a recovered report is collapsed by default ("CC výstup"-style); shorter reports
// render expanded (still inside the labelled card so the copy affordance + provenance stay consistent).
const REPORT_COLLAPSE_LINES = 12;

// Collapsible "CC výstup" card holding the agent's full markdown report (payload.report). Mirrors the
// TaskSummaryCard collapse pattern; long reports start collapsed so a thread of reports stays scannable.
function CCReport({ report }: { report: string }) {
  const lineCount = report.split("\n").length;
  const collapsible = lineCount > REPORT_COLLAPSE_LINES;
  const [expanded, setExpanded] = useState(!collapsible);
  const [copy, isCopied] = useCopyToClipboard();

  return (
    <div className="mt-2 overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface-hover)]">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--color-border-default)] px-3 py-1.5">
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className="flex items-center gap-1.5 text-left"
          aria-expanded={expanded}
        >
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
          ) : (
            <ChevronUp className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
          )}
          <span className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
            CC výstup
          </span>
          {collapsible ? (
            <span className="text-[10px] text-[var(--color-text-muted)]">· {lineCount} riadkov</span>
          ) : null}
        </button>
        <button
          type="button"
          onClick={() => copy(report)}
          className="flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text-primary)]"
          title={isCopied ? "Skopírované" : "Kopírovať"}
        >
          {isCopied ? (
            <>
              <Check className="h-3 w-3 text-[var(--color-status-success)]" />
              <span className="text-[var(--color-status-success)]">Skopírované</span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              <span>Kopírovať</span>
            </>
          )}
        </button>
      </div>
      {expanded ? (
        <div className={`${REPORT_PROSE_CLASS} px-3 py-2`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={REPORT_MARKDOWN_COMPONENTS}>
            {report}
          </ReactMarkdown>
        </div>
      ) : null}
    </div>
  );
}

// One labelled list section beneath the body (deliverables / findings / commits) — mirrors the
// TaskSummaryCard section styling (uppercase micro-label + bulleted list).
function ReportSection({ label, items, mono = false }: { label: string; items: string[]; mono?: boolean }) {
  return (
    <div>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
        {label}
      </div>
      <ul className="list-disc space-y-0.5 pl-4 text-xs text-[var(--color-text-secondary)]">
        {items.map((item, i) => (
          <li key={i} className={mono ? "break-all font-mono" : undefined}>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

const HEADLINE_CAP = 140;

// v0.7.4: guarantee a Director headline in the FE (the model systematically ignores the prompt nudge).
// Derive a prominent lead from `content` and strip it from the body so it isn't shown twice:
//   1. ATX markdown heading on the first line (`#`…`######`) → its text (without the `#`s);
//   2. else a multi-line message → the first line;
//   3. else (single line) → the first sentence (up to the first `. ` / end), capped at ~140 chars.
function deriveBrief(content: string): { headline: string; body: string } {
  const text = content.replace(/^\s+/, "");
  const nl = text.indexOf("\n");
  const firstLine = nl === -1 ? text : text.slice(0, nl);
  const rest = nl === -1 ? "" : text.slice(nl + 1).trim();

  // Rule 1: ATX heading → its text (drop leading `#`s and any closing `#`s).
  const heading = firstLine.match(/^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$/);
  if (heading?.[1]) {
    return { headline: heading[1].trim(), body: rest };
  }

  // Rule 2: multi-line → the first line leads, the remainder is the body.
  if (nl !== -1) {
    return { headline: firstLine.trim(), body: rest };
  }

  // Rule 3: single line → the first sentence (`. ` or end), capped at ~140 chars (word boundary preferred).
  const sentence = firstLine.match(/^(.+?\.)(?:\s|$)/);
  let cut = sentence?.[1] ? sentence[1].length : firstLine.length;
  let ellipsis = false;
  if (cut > HEADLINE_CAP) {
    const lastSpace = firstLine.slice(0, HEADLINE_CAP).lastIndexOf(" ");
    cut = lastSpace > 80 ? lastSpace : HEADLINE_CAP;
    ellipsis = true;
  }
  return {
    headline: firstLine.slice(0, cut).trimEnd() + (ellipsis ? "…" : ""),
    body: firstLine.slice(cut).trim(),
  };
}

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
  // CR-2 (v0.7.3): a Director-facing brief (relay / verify turn, payload.is_director_brief) — shares the
  // synthesis's prominent primary rail (it's a Coordinator→Director decision message), badged "Na rade".
  const isDirectorBrief = Boolean((message.payload as { is_director_brief?: boolean } | null)?.is_director_brief);
  // Both is_synthesis and is_director_brief get the PRIMARY rail (mutually exclusive: synthesis vs relay/verify).
  const isPrimaryBrief = isSynthesis || isDirectorBrief;
  // v0.7.4: the Director-facing briefs get a FE-guaranteed prominent headline + stripped markdown body.
  const brief = isPrimaryBrief ? deriveBrief(message.content) : null;
  const isRawReport = message.kind === "gate_report" && message.author !== "coordinator" && !isSynthesis;

  // Legible-cockpit-output fix: the agent's recovered full markdown report (payload.report) is the rich
  // body source; the structured payload arrays render as labelled sections beneath it. All additive — a
  // message without them keeps the exact prior content/summary rendering.
  const payload = message.payload as Record<string, unknown> | null;
  const report = typeof payload?.report === "string" ? payload.report.trim() : "";
  const asStrings = (v: unknown): string[] =>
    Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.trim() !== "") : [];
  const deliverables = asStrings(payload?.deliverables);
  const commits = asStrings(payload?.commits);
  const findings = asStrings(payload?.findings);
  const hasSections = deliverables.length > 0 || commits.length > 0 || findings.length > 0;

  const badge = KIND_BADGE[message.kind] ?? "bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]";
  // Synthesis / Director-brief → prominent primary rail; autonomous decision → amber attention rail; else the
  // per-author accent (dimmed for a raw report).
  const container = isPrimaryBrief
    ? "rounded-r-lg border-l-[6px] border-primary-500 bg-primary-500/10 ring-1 ring-primary-500/20 px-3 py-2.5 text-sm"
    : isAutonomous
      ? "rounded-r-lg border-l-[6px] border-amber-500 bg-amber-500/10 px-3 py-2.5 text-sm"
      : `rounded-r-lg border-l-4 ${PARTICIPANT_ACCENT[message.author] ?? PARTICIPANT_ACCENT.system} px-3 py-2 text-sm${
          isRawReport ? " opacity-60" : ""
        }`;

  return (
    <div className={container}>
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
          <span aria-hidden="true">{PARTICIPANT_EMOJI[message.author]}</span>
          <span className="font-semibold">{ROLE_LABELS[message.author]}</span>
          <span className="text-[var(--color-text-muted)]">→</span>
          <span className="text-[var(--color-text-secondary)]">{ROLE_LABELS[message.recipient]}</span>
          <span className="text-[var(--color-text-muted)]">·</span>
          <span className="font-mono text-[10px] text-[var(--color-text-muted)]">{ts}</span>
        </div>
        {isAutonomous ? (
          <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold bg-amber-500/20 text-amber-700 dark:text-amber-200">
            {AUTONOMOUS_LABEL}
          </span>
        ) : isSynthesis ? (
          <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold bg-primary-500/20 text-primary-700 dark:text-primary-200">
            {SYNTHESIS_LABEL}
          </span>
        ) : isDirectorBrief ? (
          <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold bg-primary-500/20 text-primary-700 dark:text-primary-200">
            {DIRECTOR_BRIEF_LABEL}
          </span>
        ) : (
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${badge}`}>
            {isRawReport ? RAW_REPORT_LABEL : message.kind}
          </span>
        )}
      </div>
      {/* Director-facing briefs keep their FE-guaranteed prominent headline above the body. */}
      {brief ? (
        <div className="text-[0.9375rem] font-semibold leading-snug text-[var(--color-text-primary)]">
          {brief.headline}
        </div>
      ) : null}

      {/* Body: the recovered full agent report (rich + collapsible "CC výstup") takes precedence; else the
          brief's stripped markdown body; else the plain message content. */}
      {report ? (
        <CCReport report={report} />
      ) : brief ? (
        brief.body ? (
          <div className={`${PROSE_CLASS} mt-1.5`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{brief.body}</ReactMarkdown>
          </div>
        ) : null
      ) : (
        <div className={PROSE_CLASS}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
        </div>
      )}

      {/* Structured payload sections beneath the body — labelled, mirrors TaskSummaryCard. */}
      {hasSections ? (
        <div className="mt-2 space-y-2">
          {deliverables.length > 0 ? <ReportSection label="Výstupy" items={deliverables} /> : null}
          {findings.length > 0 ? <ReportSection label="Zistenia" items={findings} /> : null}
          {commits.length > 0 ? <ReportSection label="Commity" items={commits} mono /> : null}
        </div>
      ) : null}
    </div>
  );
}

export default PipelineMessageBubble;
