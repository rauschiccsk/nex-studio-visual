// ReverifyBar — the drift re-verify surface ("Over znova", CR-V2-057).
//
// CR-V2-056 reality-anchoring: a version's "overená" (verified) green is computed LIVE from the repo — the
// PASS-bound commit SHA vs current HEAD. When the code moves PAST the verified commit the board reports
// `verified_provenance === "sha_drift"`: the version WAS verified, but the green no longer reflects the current
// code. Until now the cockpit shipped that fact on the board but rendered NOTHING — no warning, no action (a
// drifted `done`/Hotovo version had an EMPTY action set), so a non-expert Manažér would trust a stale green.
// The backend `overit_znovu` handler existed + worked but was never offered anywhere.
//
// This bar closes both halves of the kernel gap: (1) LEGIBILITY — an honest plain-language stale-PASS warning;
// (2) ACTIONABILITY — a one-click "Over znova" that re-runs the independent Auditor against current HEAD (a
// clean re-verify, no fix, no rebuild), instead of the heavier Upraviť fix-loop.
//
// Honest-by-construction (mirrors SchvalitBar): renders NOTHING unless the backend OFFERS `overit_znovu` right
// now — which the board route does ONLY when the live provenance is `sha_drift` AND the state is settled
// (done / awaiting_manazer). So the warning shows exactly when re-verify is both meaningful and available.

import { useState } from "react";
import { CircleAlert, RotateCw } from "lucide-react";

import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

export default function ReverifyBar({ board, versionId, onBoard }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  // Honest-by-construction gate: the bar exists ONLY when the backend offers `overit_znovu` right now (a
  // settled version whose verified green has drifted past current HEAD).
  if (!board?.available_actions?.includes("overit_znovu")) return null;

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const nextBoard = await postPipelineActionApi(versionId, { action: "overit_znovu" });
      onBoard(nextBoard);
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Opätovné overenie zlyhalo"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="border-t border-[var(--color-border-default)] bg-[var(--color-surface)]">
      {/* Honest stale-PASS warning — the green "overená" no longer reflects the current code. */}
      <div className="flex items-center gap-2 border-l-4 border-l-[var(--color-state-warning-fg)] bg-[var(--color-state-warning-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--color-state-warning-fg)]">
        <CircleAlert className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span>Overenie je zastarané — kód sa odvtedy zmenil</span>
      </div>

      <div className="flex flex-col gap-2 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-[var(--color-text-muted)]">
            Táto verzia už bola overená, ale kód sa odvtedy posunul za overený bod — zelené „overená" už nemusí
            platiť. „Over znova" nechá Audítora zopakovať overenie proti aktuálnemu kódu (bez opravy, bez novej
            stavby).
          </p>
          <button
            type="button"
            onClick={submit}
            disabled={submitting}
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RotateCw className={`h-3.5 w-3.5 ${submitting ? "animate-spin" : ""}`} aria-hidden="true" />
            {submitting ? "Overujem…" : "Over znova"}
          </button>
        </div>
        <ErrorNote error={error} />
      </div>
    </div>
  );
}
