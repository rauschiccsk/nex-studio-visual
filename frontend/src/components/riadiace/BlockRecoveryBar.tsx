// BlockRecoveryBar — the recovery surface for a pipeline BLOCKED on something the Manažér must resolve by
// clicking (self-sufficiency kernel, audit Theme 1). Before this, a build that hit an error settled
// `blocked` + block_reason ∈ {agent_error, system_error, parse_exhaustion}, the status read a generic "Čaká na
// súhlas", and NO action bar rendered — the offered verbs (uprav/answer/ask) had no button anywhere, so the
// only way forward was guessing that free-text in the composer re-dispatches. A non-expert was dead-ended.
//
// This bar closes that: it names WHAT happened in plain Slovak (BLOCK_REASON_LABELS), shows the engine's own
// ready-made "čo ďalej" guidance (state.next_action — previously rendered nowhere), and gives the clickable
// recovery:
//   • an ERROR (agent_error / system_error / parse_exhaustion) → "Skús znova" (→ uprav, the engine's re-work
//     recovery; a steer note is optional — a canned retry brief is sent when empty, since uprav needs a comment);
//   • an agent QUESTION (agent_question) → "Odpovedať" (→ answer, the answer text is required).
// framework_issue (→ Dedo) and decision_needed (→ Decision Cards) are handled elsewhere and excluded here.
//
// Honest-by-construction (mirrors the other bars): renders NOTHING unless the pipeline is blocked on one of
// these manager-resolvable reasons.

import { useState } from "react";
import { CircleAlert, MessageCircle, RotateCw } from "lucide-react";

import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import { useDraft, draftKey } from "@/hooks/useDraft";

import {
  postPipelineActionApi,
  type PipelineBoard,
} from "@/services/api/pipeline";
import { BLOCK_REASON_LABELS } from "@/components/cockpit/labels";
import {
  blockRecoveryOwnsInput,
  isCheckReason,
  isErrorReason,
  DEFAULT_CHECK_FIX,
  DEFAULT_RETRY,
} from "@/components/riadiace/blockRecovery";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

export default function BlockRecoveryBar({ board, versionId, onBoard }: Props) {
  // ICCINT-30: an answer to the agent survives a trip to Dokumenty; cleared only on a successful send.
  const {
    text,
    setText,
    clear: clearDraft,
    restored,
  } = useDraft(draftKey("odpoved", versionId));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  // Called BEFORE the honest-by-construction early return below — a hook after a conditional return
  // is a hook that sometimes does not run, which React forbids.
  const growRef = useAutoGrowTextarea(text);

  const state = board?.state ?? null;
  const reason = state?.block_reason ?? null;
  const isQuestion = reason === "agent_question";
  const isError = isErrorReason(reason);
  const isCheck = isCheckReason(reason);

  // Honest-by-construction gate: only a block the Manažér can resolve by clicking here.
  if (!state || !blockRecoveryOwnsInput(state)) return null;

  const headline =
    (reason && BLOCK_REASON_LABELS[reason]) ||
    "Niečo si vyžaduje tvoju pozornosť";
  const guidance = (state.next_action || "").trim();
  // A question's answer is required; an error's steer is optional (a canned retry brief covers the empty case).
  const canSubmit =
    !submitting && (isError || isCheck || text.trim().length > 0);

  async function submit() {
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      const trimmed = text.trim();
      const req = isQuestion
        ? { action: "answer" as const, payload: { text: trimmed } }
        : {
            action: "uprav" as const,
            payload: {
              comment: trimmed || (isCheck ? DEFAULT_CHECK_FIX : DEFAULT_RETRY),
            },
          };
      const nextBoard = await postPipelineActionApi(versionId, req);
      onBoard(nextBoard);
      clearDraft();
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Akcia zlyhala"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="border-t border-[var(--color-border-default)] bg-[var(--color-surface)]">
      {/* WHAT happened — plain Slovak, coloured by kind (error = red, question = accent/"your turn"). */}
      <div
        className={`flex items-center gap-2 border-l-4 px-4 py-2.5 text-sm font-semibold ${
          isError
            ? "border-l-[var(--color-status-error)] bg-[var(--color-state-error-bg)] text-[var(--color-state-error-fg)]"
            : isCheck
              ? "border-l-[var(--color-state-warning-fg)] bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]"
              : "border-l-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]/10 text-[var(--color-accent-primary)]"
        }`}
      >
        {isError || isCheck ? (
          <CircleAlert className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        ) : (
          <MessageCircle className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        )}
        {/* ICCINT-43: "Niečo zlyhalo — " belongs ONLY to a real error. A negative check result is not a
            failure of anybody, and prefixing it that way is how the Director came to report a working agent
            as broken. The check's own headline stands alone; `next_action` below says what was measured. */}
        <span>{isError ? `Niečo zlyhalo — ${headline}` : headline}</span>
      </div>

      <div className="flex flex-col gap-2 px-4 py-3">
        {/* The engine's own ready-made "čo ďalej" guidance. For an ERROR it's the recovery hint. For a
            QUESTION it repeats the whole question — which already renders as the "Otázka — na rade si ty"
            card in the thread right above — so it is SUPPRESSED here (nex-studio-visual crash-test
            2026-07-13: the triple-rendered question read as clutter). */}
        {!isQuestion && guidance && (
          <p className="text-xs text-[var(--color-text-muted)]">{guidance}</p>
        )}

        {restored && (
          // ICCINT-30: text that appears by itself must be recognisable as HIS earlier draft — not as
          // something someone else wrote into his box while he was away.
          <p className="text-[11px] text-[var(--color-text-muted)]">
            Obnovený rozpísaný text — pokračuj, alebo ho prepíš.
          </p>
        )}
        <div className="flex items-center gap-2">
          <textarea
            lang="sk"
            spellCheck={true}
            ref={growRef}
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={
              isQuestion
                ? "Tvoja odpoveď… (Enter odošle, Shift+Enter nový riadok)"
                : "Usmernenie k oprave (nepovinné) — Enter odošle, Shift+Enter nový riadok"
            }
            disabled={submitting}
            className="flex-1 resize-none rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-canvas)] px-3 py-1.5 text-xs text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:border-primary-500 focus:outline-none disabled:opacity-60"
            onKeyDown={(e) => {
              // Shift+Enter is a newline; plain Enter sends. Matches ConversationComposer — the other
              // box the Manažér writes to — because two message boxes that answer the same key
              // differently is a trap you only find by losing a paragraph.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (canSubmit) submit();
              }
            }}
          />
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isError && (
              <RotateCw
                className={`h-3.5 w-3.5 ${submitting ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
            )}
            {submitting
              ? isQuestion
                ? "Odosielam…"
                : "Skúšam…"
              : isQuestion
                ? "Odpovedať"
                : "Skús znova"}
          </button>
        </div>

        <ErrorNote error={error} />
      </div>
    </div>
  );
}
