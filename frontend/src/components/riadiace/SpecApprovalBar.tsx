// SpecApprovalBar — the "Schváliť Špecifikáciu" moment in the Riadiace centrum (spine STEP 2).
//
// Honest-by-construction: renders NOTHING unless the backend currently OFFERS ``approve_spec`` in
// ``board.available_actions`` (a settled Príprava). approve_spec is dial-INDEPENDENT — the end-Príprava
// approval is always mandatory. On click it FREEZES the on-disk Špecifikácia as the binding source of truth;
// in a conversation build the rozhovor then CONTINUES (there is no Návrh phase to enter — MD-2 rec A), so the
// consequence copy reads "zmrazí ... v rozhovore pokračujeme ďalej", NOT "začne fáza Návrh".

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileText } from "lucide-react";

import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

export default function SpecApprovalBar({ board, versionId, onBoard }: Props) {
  const navigate = useNavigate();
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Honest-by-construction gate: the bar exists ONLY when the backend offers the approval right now.
  if (!board?.available_actions?.includes("approve_spec")) return null;

  async function handleApprove() {
    setError("");
    setSubmitting(true);
    try {
      const trimmed = comment.trim();
      const nextBoard = await postPipelineActionApi(versionId, {
        action: "approve_spec",
        payload: trimmed ? { comment: trimmed } : undefined,
      });
      onBoard(nextBoard);
      setComment("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Schválenie zlyhalo.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-[var(--color-text-muted)]">
          Schválením sa Špecifikácia zmrazí ako záväzný podklad; v rozhovore pokračujeme ďalej.
        </p>
        <button
          type="button"
          onClick={() => navigate("/specifikacia")}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)]"
        >
          <FileText className="h-3.5 w-3.5" />
          Prezrieť Špecifikáciu
        </button>
      </div>

      <div className="flex items-center gap-2">
        <input
          lang="sk"
          spellCheck={false}
          type="text"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Komentár k schváleniu (voliteľné)"
          disabled={submitting}
          className="flex-1 rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-canvas)] px-3 py-1.5 text-xs text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:border-primary-500 focus:outline-none disabled:opacity-60"
        />
        <button
          type="button"
          onClick={handleApprove}
          disabled={submitting}
          className="shrink-0 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Schvaľujem…" : "Schváliť Špecifikáciu"}
        </button>
      </div>

      {error && <p className="text-xs text-[var(--color-status-error)]">{error}</p>}
    </div>
  );
}
