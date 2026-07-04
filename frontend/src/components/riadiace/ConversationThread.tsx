// ConversationThread — the SPINE: the event-rendered 1:1 transcript at the centre of the Riadiace centrum
// (spine STEP 1). Salvaged near-verbatim from the proven (now-CUT) agent transcript component per the
// SALVAGE_VS_FRESH design choice — the bubble/question/report rendering was tuned over several CRs and the
// spine reuses it unchanged rather than rewriting a conversation UI from scratch.
//
// The live view is an EVENT-RENDERED thread built from the engine's stream-json broadcast over the pipeline
// WS (durable `recent_messages` + the ephemeral live `activity` lines), NOT a raw-ANSI byte model. The raw
// xterm survives only for the dormant break-glass debug PTY (a separate render path owned by
// PersistentTerminalsLayer) — the two are deliberately NOT conflated.
//
// The thread reads like a Director↔Dedo session: each persisted PipelineMessage is a bubble keyed by its
// author (the AI Agent / Auditor / Manažér / system), and while the agent is working the live activity feed
// (reads, writes, tool calls) streams below the last bubble.

import { useEffect, useRef } from "react";

import { SpecMarkdown } from "../markdown/SpecMarkdown";
import type { ActivityLine, PipelineMessage, PipelineParticipant } from "../../services/api/pipeline";
import PipelineActivityFeed from "../cockpit/PipelineActivityFeed";
import { ROLE_LABELS } from "../cockpit/labels";

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

export function ConversationThread({ messages, activity, working }: Props) {
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
          <p>Napíš AI Agentovi nižšie — začnite rozhovor o projekte.</p>
        </div>
      ) : (
        <ul className="space-y-3">
          {messages.map((m) => {
            const right = isOperator(m.author);
            // CR-V2-032: render the agent's FULL human-readable body (``payload.report``) — ``content`` is
            // only the one-line summary (deriveBrief / previews / lists). When the turn is a question, the
            // actual ask lives in ``payload.question``; surface it as a highlighted "your turn" block so the
            // thread reads like a real dialogue (the questions were previously invisible).
            const report = typeof m.payload?.report === "string" ? (m.payload.report as string) : "";
            const question = typeof m.payload?.question === "string" ? (m.payload.question as string) : "";
            const body = report.trim() || m.content;
            return (
              <li key={m.id} className={`flex ${right ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[85%] rounded-lg border px-3 py-2 ${bubbleClass(m.author)}`}>
                  <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                    <span className="font-semibold text-[var(--color-text-secondary)]">{authorLabel(m.author)}</span>
                  </div>
                  <SpecMarkdown
                    body={body}
                    className="prose prose-sm dark:prose-invert max-w-none text-sm text-[var(--color-text-primary)]"
                  />
                  {question.trim() && (
                    <div className="mt-2 rounded-md border-l-2 border-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]/5 px-3 py-2">
                      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-accent-primary)]">
                        Otázka — na rade si ty
                      </div>
                      <SpecMarkdown
                        body={question}
                        className="prose prose-sm dark:prose-invert max-w-none text-sm text-[var(--color-text-primary)]"
                      />
                    </div>
                  )}
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

export default ConversationThread;
