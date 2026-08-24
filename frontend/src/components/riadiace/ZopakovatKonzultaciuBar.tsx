// ZopakovatKonzultaciuBar — the way back after the Decision Cards could not be built (ICCINT-25).
//
// When the independent review finds holes, the engine asks the AI Agent to turn them into Decision Cards —
// one question at a time, with options and a recommendation. If that ONE turn does not come back (the model
// briefly unreachable, an unparseable answer), the engine falls open to a plain stop with the findings listed
// as text. The fail-open is right; nothing is lost. What was missing is the way BACK: found by the Director
// 24.08.2026 on nex-productcatalogs, an outage took both attempts and, once it passed, `navrh` /
// `awaiting_manazer` offered `ask` / `schvalit` / `uprav` and nothing else. A passing failure had permanently
// downgraded HOW he decides — from cards to a wall of eleven findings under two buttons.
//
// Honest-by-construction (mirrors the other riadiace bars): renders NOTHING unless the backend currently
// OFFERS `zopakovat_konzultaciu`, which it does only where the cards were never written — never after a
// dispute (the agent answered, and asking again re-asks an answered question) and never past the re-consult
// cap.

import { useState } from "react";
import { Layers, RotateCw } from "lucide-react";

import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

export default function ZopakovatKonzultaciuBar({ board, versionId, onBoard }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  // Honest-by-construction gate: the bar exists ONLY when the backend offers the verb right now.
  if (!board?.available_actions?.includes("zopakovat_konzultaciu")) return null;

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const nextBoard = await postPipelineActionApi(versionId, { action: "zopakovat_konzultaciu" });
      onBoard(nextBoard);
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Konzultáciu sa nepodarilo vyžiadať"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="border-t border-[var(--color-border-default)] bg-[var(--color-surface)]">
      <div className="flex items-center gap-2 border-l-4 border-l-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]/10 px-4 py-2.5 text-sm font-semibold text-[var(--color-accent-primary)]">
        <Layers className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span>Rozhodnutia sa nepodarilo pripraviť</span>
      </div>

      <div className="flex flex-col gap-2 px-4 py-3">
        <p className="text-xs text-[var(--color-text-muted)]">
          Nálezy máš nižšie vypísané, takže sa nič nestratilo — ale namiesto rozhodnutí, ktoré sa dajú
          odklikať jedno po druhom, ostal len zoznam. Odpoveď vtedy neprišla. Skús to znova; ak sa to
          nepodarí ani teraz, návrh sa dá posúdiť aj klasicky.
        </p>
        <div className="flex items-center justify-end">
          <button
            type="button"
            onClick={submit}
            disabled={submitting}
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RotateCw className={`h-3.5 w-3.5 ${submitting ? "animate-spin" : ""}`} aria-hidden="true" />
            {submitting ? "Pripravujem…" : "Skúsiť konzultáciu znova"}
          </button>
        </div>
        <ErrorNote error={error} />
      </div>
    </div>
  );
}
