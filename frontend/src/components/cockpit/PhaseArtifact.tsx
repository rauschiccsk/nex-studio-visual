// A phase's durable artifact (CR-V2-021, design §4.4.2 "Tab contents — kept forever, per version").
//
// Each phase persists its output as a durable record: the phase's gate_report / verdict message carries the
// human-readable markdown in ``payload.report`` (PREP = Špecifikácia, NÁVRH = design doc incl. task plan,
// VERIFIKÁCIA = Auditor verdict). This renders the LATEST such artifact for the viewed phase as Markdown so
// a finished phase stays viewable after the build completes (no vanish — the old task-plan pain). When there
// is no artifact yet (the phase hasn't produced its report), the panel shows a phase-appropriate placeholder.

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { CodeBlock } from "../markdown/CodeBlock";
import type { PipelineMessage } from "../../services/api/pipeline";
import type { BuildPhase } from "./labels";

// Fenced code → the shared CodeBlock (language label + copy); everything else default GFM.
const MARKDOWN_COMPONENTS: Components = {
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || "");
    const inline = !className;
    if (!inline && match) return <CodeBlock language={match[1]}>{String(children)}</CodeBlock>;
    if (!inline) return <CodeBlock>{String(children)}</CodeBlock>;
    return (
      <code className="rounded bg-[var(--color-surface-hover)] px-1 py-0.5 text-[0.85em]" {...props}>
        {children}
      </code>
    );
  },
};

// The latest message for ``phase`` carrying a renderable artifact body (``payload.report`` — the durable
// markdown — or, as a fallback, the message ``content`` of the phase's gate_report / verdict turn).
export function latestPhaseArtifact(messages: PipelineMessage[], phase: BuildPhase): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m || m.stage !== phase) continue;
    if (m.kind !== "gate_report" && m.kind !== "verdict") continue;
    const report = (m.payload as { report?: string } | null)?.report;
    const body = (report && report.trim()) || (m.content && m.content.trim());
    if (body) return body;
  }
  return null;
}

interface Props {
  phase: BuildPhase;
  messages: PipelineMessage[];
  placeholder: string;
}

export function PhaseArtifact({ phase, messages, placeholder }: Props) {
  const body = latestPhaseArtifact(messages, phase);
  if (!body) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-xs text-[var(--color-text-muted)]">
        {placeholder}
      </div>
    );
  }
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none px-4 py-3 text-sm text-[var(--color-text-primary)]">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
        {body}
      </ReactMarkdown>
    </div>
  );
}

export default PhaseArtifact;
