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
  return ROLE_LABELS[author] ?? "Neznáma rola";
}

// Per-author bubble accent — keeps the AI Agent visually distinct from the Auditor / system notices, reusing
// the unified token palette (no raw pastels).
function bubbleClass(author: PipelineParticipant, frameworkIssue = false): string {
  // Director observation #6: an agent → Dedo escalation (framework_issue) is a system message that must
  // STAND OUT — the build is hard-blocked on a NEX Studio fix. Red accent, regardless of author.
  if (frameworkIssue)
    return "bg-[var(--color-state-error-bg)] border-[var(--color-state-error-fg)]/40";
  if (author === "manazer")
    return "bg-[var(--color-accent-primary)]/10 border-[var(--color-accent-primary)]/30";
  if (author === "auditor")
    return "bg-[var(--color-state-warning-bg)] border-[var(--color-state-warning-fg)]/20";
  // ICCINT-12: Dedo answering a framework_issue escalation. Green — the counterpart of the red escalation
  // bubble above (the block has an answer), and the only green in the thread, so it can never be confused
  // with the neutral `system` notice, the amber Auditor or the indigo Manažér.
  if (author === "dedo")
    return "bg-[var(--color-state-success-bg)] border-[var(--color-state-success-fg)]/40";
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

  // Self-sufficiency kernel: hide internal build-loop chatter the Manažér never needs. The per-attempt
  // self-check `return` messages (author=system, recipient=ai_agent, kind="return") are the AI Agent's own
  // self-talk — raw, technical (often English: "deliverable 'x' missing on disk"), and up to 5 per failed task.
  // The manager gets the plain per-task summary instead ("Úloha #N zlyhala po 5 pokusoch"). Filtered from the
  // manager thread ONLY — the rows stay in the board/DB for the agent loop + break-glass debug.
  //
  // `author === "system"` was MISSING from this predicate until ICCINT-24, and the omission hid the wrong
  // thing: the Manažér's own "Uprav" is recorded manazer→ai_agent kind="return", so every steer he sent
  // vanished from his transcript — he pressed a button and the conversation showed nothing. Found because
  // the one-click send below lands on that exact shape. The rule is unchanged, it now matches its own
  // description: the AI Agent's SYSTEM self-talk is hidden, the human's messages are not.
  //
  // FOR THE DIRECTOR — THIS IS A SEPARATE DECISION FROM ICCINT-24, and it is flagged rather than slipped in
  // (audit 2026-08-23, finding 4). It is a change of behaviour on an EXISTING screen and it is retroactive:
  // transcripts of builds finished long ago will now show bubbles that were never displayed before — every
  // "Uprav" their Manažér ever sent. Nothing is rewritten (the rows were always in the database, only
  // filtered on the way to the screen), but what people see when they reopen an old build does change.
  //
  // It is nonetheless in this bundle, because ICCINT-24 does not work without it: the one-click send writes
  // exactly that shape, so with the old predicate the Manažér would press "Vrátiť agentovi" and see nothing
  // appear — the feature's own output would be invisible. Measured, not assumed: with the previous
  // predicate restored, `test_ConversationThread.test.tsx` fails on the sent-proposal bubble and on the
  // Manažér's own steer (2 failed / 5 passed). If the Director would rather keep old transcripts exactly as
  // they read today, the narrowing has to be scoped to messages carrying `dedo_proposal_origin` instead —
  // which would leave every OTHER steer he sends still invisible, i.e. the bug intact.
  //
  // ICCINT-24: a Dedo PROPOSAL is filtered out for a different reason — it was never said to the agent. It
  // is a finding on the Manažér's desk (DedoProposalBar owns it, editable, behind his click); showing it as
  // a bubble in the transcript would claim a message reached the agent that by construction did not. Once
  // he sends it, what enters the thread is HIS message, stamped with where the wording came from (below).
  const visibleMessages = messages.filter(
    (m) =>
      !(m.author === "system" && m.kind === "return" && m.recipient === "ai_agent") &&
      m.payload?.dedo_proposal !== true,
  );

  // Auto-scroll to the newest bubble / activity line (a live console follows the bottom).
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [visibleMessages.length, activity.length, working]);

  const empty = visibleMessages.length === 0 && !working;

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-y-auto bg-[var(--color-canvas)] px-4 py-3">
      {empty ? (
        <div className="flex h-full flex-col items-center justify-center gap-1 text-center text-xs text-[var(--color-text-muted)]">
          <p>Zatiaľ žiadna konverzácia.</p>
          <p>Napíš AI Agentovi nižšie — začnite rozhovor o projekte.</p>
        </div>
      ) : (
        <ul className="space-y-3">
          {visibleMessages.map((m) => {
            const right = isOperator(m.author);
            // CR-V2-032: render the agent's FULL human-readable body (``payload.report``) — ``content`` is
            // only the one-line summary (deriveBrief / previews / lists). When the turn is a question, the
            // actual ask lives in ``payload.question``; surface it as a highlighted "your turn" block so the
            // thread reads like a real dialogue (the questions were previously invisible).
            const report = typeof m.payload?.report === "string" ? (m.payload.report as string) : "";
            const question = typeof m.payload?.question === "string" ? (m.payload.question as string) : "";
            const body = report.trim() || m.content;
            // STEP 5 (Kontrola, K-2): a kontrola report carries `payload.kontrola === true` + Pevné/Vratké
            // counts. Render a slim two-chip header ABOVE the full plain report (which renders unchanged below).
            // Degrade gracefully: no flag / no counts → no chips (a normal message is untouched).
            const isKontrola = m.payload?.kontrola === true;
            const solidCount = typeof m.payload?.solid_count === "number" ? (m.payload.solid_count as number) : null;
            const shakyCount = typeof m.payload?.shaky_count === "number" ? (m.payload.shaky_count as number) : null;
            // Director observation #6: the agent → Dedo escalation system message — accent it red and surface
            // the message that went to Dedo (payload.dedo_message) so the Manažér sees exactly what escalated.
            const isFrameworkIssue = m.payload?.framework_issue === true;
            const dedoMessage =
              typeof m.payload?.dedo_message === "string" ? (m.payload.dedo_message as string) : "";
            // Plain-language failure framing: when the manager-facing body is the humanised WHY, the raw
            // technical reason (a boot probe error, the smoke script's tail) rides in payload.technical_detail —
            // render it under a collapsible so it's available on demand, never in the manager's face.
            const technicalDetail =
              typeof m.payload?.technical_detail === "string" ? (m.payload.technical_detail as string) : "";
            // ICCINT-24: this Manažér message started as a finding from Dedo (DedoProposalBar). Say so on the
            // bubble — and when the Manažér rewrote it before sending, keep Dedo's ORIGINAL wording visible
            // here, so "what did the technical team propose vs. what actually went out" is answerable in the
            // thread itself, not only in the database.
            const proposalOrigin =
              m.payload?.dedo_proposal_origin && typeof m.payload.dedo_proposal_origin === "object"
                ? (m.payload.dedo_proposal_origin as { original_content?: string })
                : null;
            const proposalOriginal = (proposalOrigin?.original_content ?? "").trim();
            const proposalEdited = !!proposalOrigin && proposalOriginal !== m.content.trim();
            return (
              <li key={m.id} className={`flex ${right ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[85%] rounded-lg border px-3 py-2 ${bubbleClass(m.author, isFrameworkIssue)}`}>
                  <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                    <span className="font-semibold text-[var(--color-text-secondary)]">{authorLabel(m.author)}</span>
                    {isFrameworkIssue && (
                      <span className="rounded-full bg-[var(--color-state-error-fg)]/15 px-1.5 py-0.5 font-semibold text-[var(--color-state-error-fg)]">
                        Chyba NEX Studia
                      </span>
                    )}
                  </div>
                  {isKontrola && (solidCount !== null || shakyCount !== null) && (
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      {solidCount !== null && (
                        <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                          Pevné · {solidCount}
                        </span>
                      )}
                      {shakyCount !== null && (
                        <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                          <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                          Vratké · {shakyCount}
                        </span>
                      )}
                    </div>
                  )}
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
                  {isFrameworkIssue && dedoMessage.trim() && (
                    <div className="mt-2 rounded-md border-l-2 border-[var(--color-state-error-fg)] bg-[var(--color-state-error-fg)]/5 px-3 py-2">
                      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-state-error-fg)]">
                        Nahlásené technickému tímu
                      </div>
                      <SpecMarkdown
                        body={dedoMessage}
                        className="prose prose-sm dark:prose-invert max-w-none text-sm text-[var(--color-text-primary)]"
                      />
                    </div>
                  )}
                  {proposalOrigin && (
                    <div className="mt-2 rounded-md border-l-2 border-[var(--color-state-warning-fg)] bg-[var(--color-state-warning-bg)] px-3 py-2">
                      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-state-warning-fg)]">
                        Podnet od Deda (technický tím)
                      </div>
                      <p className="text-[11px] text-[var(--color-text-muted)]">
                        {proposalEdited
                          ? "Text navrhol Dedo, odoslal si ho upravený. Jeho pôvodné znenie:"
                          : "Text navrhol Dedo, odoslal si ho bez zmeny."}
                      </p>
                      {proposalEdited && proposalOriginal && (
                        <SpecMarkdown
                          body={proposalOriginal}
                          className="prose prose-sm dark:prose-invert mt-1 max-w-none text-xs text-[var(--color-text-secondary)]"
                        />
                      )}
                    </div>
                  )}
                  {technicalDetail.trim() && (
                    <details className="mt-2 text-[11px] text-[var(--color-text-muted)]">
                      <summary className="cursor-pointer select-none hover:text-[var(--color-text-secondary)]">
                        Technický detail
                      </summary>
                      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-2 py-1.5 font-mono text-[10px] leading-relaxed text-[var(--color-text-secondary)]">
                        {technicalDetail}
                      </pre>
                    </details>
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
