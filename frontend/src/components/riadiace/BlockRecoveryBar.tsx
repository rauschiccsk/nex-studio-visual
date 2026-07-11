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

import { postPipelineActionApi, type PipelineBoard, type BlockReason } from "@/services/api/pipeline";
import { BLOCK_REASON_LABELS } from "@/components/cockpit/labels";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";

const ERROR_REASONS: BlockReason[] = ["agent_error", "system_error", "parse_exhaustion"];
const DEFAULT_RETRY = "Skús to prosím znova.";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

export default function BlockRecoveryBar({ board, versionId, onBoard }: Props) {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  const state = board?.state ?? null;
  const reason = state?.block_reason ?? null;
  const isQuestion = reason === "agent_question";
  const isError = !!reason && ERROR_REASONS.includes(reason);

  // Honest-by-construction gate: only a block the Manažér can resolve by clicking here.
  if (!state || state.status !== "blocked" || !(isQuestion || isError)) return null;

  const headline = (reason && BLOCK_REASON_LABELS[reason]) || "Niečo si vyžaduje tvoju pozornosť";
  const guidance = (state.next_action || "").trim();
  // A question's answer is required; an error's steer is optional (a canned retry brief covers the empty case).
  const canSubmit = !submitting && (isError || text.trim().length > 0);

  async function submit() {
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      const trimmed = text.trim();
      const req = isQuestion
        ? { action: "answer" as const, payload: { text: trimmed } }
        : { action: "uprav" as const, payload: { comment: trimmed || DEFAULT_RETRY } };
      const nextBoard = await postPipelineActionApi(versionId, req);
      onBoard(nextBoard);
      setText("");
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
            : "border-l-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]/10 text-[var(--color-accent-primary)]"
        }`}
      >
        {isError ? (
          <CircleAlert className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        ) : (
          <MessageCircle className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        )}
        <span>{isError ? `Niečo zlyhalo — ${headline}` : headline}</span>
      </div>

      <div className="flex flex-col gap-2 px-4 py-3">
        {/* The engine's own ready-made "čo ďalej" guidance — previously computed but never shown. */}
        {guidance && <p className="text-xs text-[var(--color-text-muted)]">{guidance}</p>}

        <div className="flex items-center gap-2">
          <input
            lang="sk"
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={isQuestion ? "Tvoja odpoveď…" : "Usmernenie k oprave (nepovinné)"}
            disabled={submitting}
            className="flex-1 rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-canvas)] px-3 py-1.5 text-xs text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:border-primary-500 focus:outline-none disabled:opacity-60"
            onKeyDown={(e) => {
              if (e.key === "Enter" && canSubmit) submit();
            }}
          />
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isError && <RotateCw className={`h-3.5 w-3.5 ${submitting ? "animate-spin" : ""}`} aria-hidden="true" />}
            {submitting ? (isQuestion ? "Odosielam…" : "Skúšam…") : isQuestion ? "Odpovedať" : "Skús znova"}
          </button>
        </div>

        <ErrorNote error={error} />
      </div>
    </div>
  );
}
