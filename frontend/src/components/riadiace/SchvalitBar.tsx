// SchvalitBar — the "Schváliť plán" moment at the Návrh/plan-approval gate (regression fix
// schvalit-approval-bar.md). A near-mirror of SpecApprovalBar for the OTHER schvaľovacie-body verb the
// backend offers.
//
// Honest-by-construction: renders NOTHING unless the backend currently OFFERS ``schvalit`` in
// ``board.available_actions`` (a Návrh gate awaiting the Manažér after the Auditor review —
// ``available_actions={uprav, ask, schvalit}``). So SpecApprovalBar (``approve_spec``) and this bar
// (``schvalit``) are mutually exclusive by construction — at most one shows.
//
// Primary "Schváliť plán" → ``schvalit`` ADVANCES Návrh → Programovanie (the build). Secondary "Upraviť" →
// ``uprav`` (same available_actions set) sends the comment back as the REWORK instruction for the phase.
// The optional comment threads into ``payload.comment`` for either action.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileText } from "lucide-react";

import { postPipelineActionApi, type PipelineBoard, type PipelineActionName } from "@/services/api/pipeline";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

export default function SchvalitBar({ board, versionId, onBoard }: Props) {
  const navigate = useNavigate();
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Honest-by-construction gate: the bar exists ONLY when the backend offers the Návrh approval right now.
  if (!board?.available_actions?.includes("schvalit")) return null;

  async function submit(action: PipelineActionName, failMsg: string) {
    setError("");
    setSubmitting(true);
    try {
      const trimmed = comment.trim();
      const nextBoard = await postPipelineActionApi(versionId, {
        action,
        payload: trimmed ? { comment: trimmed } : undefined,
      });
      onBoard(nextBoard);
      setComment("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : failMsg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-[var(--color-text-muted)]">
          Schválením potvrdíš návrh a plán; projekt sa posunie do stavby (Programovanie).
        </p>
        <button
          type="button"
          onClick={() => navigate("/specifikacia")}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)]"
        >
          <FileText className="h-3.5 w-3.5" />
          Prezrieť plán / špecifikáciu
        </button>
      </div>

      <div className="flex items-center gap-2">
        <input
          lang="sk"
          spellCheck={false}
          type="text"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Komentár k schváleniu / pokyn na úpravu (voliteľné)"
          disabled={submitting}
          className="flex-1 rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-canvas)] px-3 py-1.5 text-xs text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] focus:border-primary-500 focus:outline-none disabled:opacity-60"
        />
        <button
          type="button"
          onClick={() => submit("uprav", "Úprava zlyhala.")}
          disabled={submitting}
          className="shrink-0 rounded-lg border border-[var(--color-border-strong)] px-4 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Pracujem…" : "Upraviť"}
        </button>
        <button
          type="button"
          onClick={() => submit("schvalit", "Schválenie zlyhalo.")}
          disabled={submitting}
          className="shrink-0 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Schvaľujem…" : "Schváliť plán"}
        </button>
      </div>

      {error && <p className="text-xs text-[var(--color-status-error)]">{error}</p>}
    </div>
  );
}
