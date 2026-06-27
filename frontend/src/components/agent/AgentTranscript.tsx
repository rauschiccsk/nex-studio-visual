// AgentTranscript — the EVENT-RENDERED transcript of the AI Agent session (CR-V2-022, design §4.4.1).
//
// CRITICAL (E-critic F-risk-2): this is the v2 live view of the warm `claude` session — an event-rendered
// thread built from the engine's stream-json broadcast over the pipeline WS (durable `recent_messages` +
// the ephemeral live `activity` lines), NOT the v1 xterm raw-ANSI byte model. The raw xterm survives only
// for the break-glass debug PTY (a separate render path, owned by PersistentTerminalsLayer) — the two are
// deliberately NOT conflated.
//
// The thread reads like a Director↔Dedo session: each persisted PipelineMessage is a bubble keyed by its
// author (the AI Agent / Auditor / Manažér / system), and while the agent is working the live activity feed
// (reads, writes, tool calls) streams below the last bubble.

import { useEffect, useRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { CodeBlock } from "../markdown/CodeBlock";
import type { ActivityLine, PipelineMessage, PipelineParticipant } from "../../services/api/pipeline";
import PipelineActivityFeed from "../cockpit/PipelineActivityFeed";
import { ROLE_LABELS } from "../cockpit/labels";

// Fenced code → the shared CodeBlock (language label + copy); everything else default GFM. Mirrors
// PhaseArtifact's renderer so transcript bubbles and durable phase artifacts read identically.
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

// The Manažér's own messages align right (like an outgoing chat); everyone else aligns left.
function isOperator(author: PipelineParticipant): boolean {
  return author === "manazer";
}

function authorLabel(author: PipelineParticipant): string {
  return ROLE_LABELS[author] ?? author;
}

// Per-author bubble accent — keeps the AI Agent visually distinct from the Auditor / system notices, reusing
// the unified token palette (no raw pastels).
function bubbleClass(author: PipelineParticipant): string {
  if (author === "manazer")
    return "bg-[var(--color-accent-primary)]/10 border-[var(--color-accent-primary)]/30";
  if (author === "auditor")
    return "bg-[var(--color-state-warning-bg)] border-[var(--color-state-warning-fg)]/20";
  if (author === "system")
    return "bg-[var(--color-surface-hover)] border-[var(--color-border-default)]";
  // AI Agent (the doer) — the default surface.
  return "bg-[var(--color-surface)] border-[var(--color-border-default)]";
}

interface Props {
  messages: PipelineMessage[];
  activity: ActivityLine[];
  /** Whether the agent is actively working (drives the live activity feed below the thread). */
  working: boolean;
}

export function AgentTranscript({ messages, activity, working }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to the newest bubble / activity line (a live console follows the bottom).
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length, activity.length, working]);

  const empty = messages.length === 0 && !working;

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-y-auto bg-[var(--color-canvas)] px-4 py-3">
      {empty ? (
        <div className="flex h-full flex-col items-center justify-center gap-1 text-center text-xs text-[var(--color-text-muted)]">
          <p>Zatiaľ žiadna konverzácia.</p>
          <p>Napíš AI Agentovi nižšie alebo spusti vývoj verzie vo Vývoji.</p>
        </div>
      ) : (
        <ul className="space-y-3">
          {messages.map((m) => {
            const right = isOperator(m.author);
            return (
              <li key={m.id} className={`flex ${right ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[85%] rounded-lg border px-3 py-2 ${bubbleClass(m.author)}`}>
                  <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                    <span className="font-semibold text-[var(--color-text-secondary)]">{authorLabel(m.author)}</span>
                  </div>
                  <div className="prose prose-sm dark:prose-invert max-w-none text-sm text-[var(--color-text-primary)]">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
                      {m.content}
                    </ReactMarkdown>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {/* Live activity (reads / writes / tool calls) while the agent works — the streaming tail of the
          event-rendered transcript. */}
      {working && (
        <div className="mt-3">
          <PipelineActivityFeed activity={activity} />
        </div>
      )}

      <div ref={endRef} />
    </div>
  );
}

export default AgentTranscript;
