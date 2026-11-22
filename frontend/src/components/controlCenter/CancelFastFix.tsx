// CancelFastFix — „Zrušiť rýchlu opravu“ (DEV-491).
//
// 22.11.2026 a fast fix of Orthodox Hermenia started from the project page with the brief „Oprava chyby v dodanom
// kóde“ — the Manažér only wanted to found a version for a ticket — and the cockpit had no way to take it back.
// A fast fix that did nothing yet (still in Príprava, no agent turn running) now goes away whole with one click and
// its number is free for the next fix. The button mirrors the route: the backend says whether it takes the cancel
// (`state.cancellable`) and why not (`state.cancel_refusal`), so a disabled button always carries its reason.

import { useState } from "react";
import { Trash2 } from "lucide-react";

import { cancelFastFixApi, type PipelineBoard } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  versionNumber: string;
  /** The fix is gone — leave its Riadiace centrum. */
  onCancelled: () => void;
}

const SECONDARY =
  "rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium " +
  "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-40";

export default function CancelFastFix({ board, versionId, versionNumber, onCancelled }: Props) {
  const [asking, setAsking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const [planeWarning, setPlaneWarning] = useState<string | null>(null);

  const state = board?.state;
  if (!state || state.flow_type !== "fast_fix" || state.current_stage === "done") return null;
  const cancellable = state.cancellable === true;

  async function confirm() {
    setError(null);
    setSubmitting(true);
    try {
      const result = await cancelFastFixApi(versionId);
      if (result.plane_warning) setPlaneWarning(result.plane_warning);
      else onCancelled();
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Zrušenie rýchlej opravy zlyhalo"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="flex justify-end border-b border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-1.5">
        <button
          type="button"
          onClick={() => setAsking(true)}
          disabled={!cancellable}
          title={cancellable ? undefined : (state.cancel_refusal ?? undefined)}
          className={`flex items-center gap-1.5 ${SECONDARY}`}
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          Zrušiť rýchlu opravu
        </button>
      </div>

      {asking && (
        <div
          className="fixed inset-0 z-50 flex overflow-y-auto bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="cancel-fast-fix-title"
        >
          <div className="m-auto w-full max-w-lg rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-5 shadow-xl">
            <h2 id="cancel-fast-fix-title" className="text-sm font-semibold text-[var(--color-text-primary)]">
              Zrušiť rýchlu opravu {versionNumber}
            </h2>
            {planeWarning ? (
              <>
                <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
                  Rýchla oprava {versionNumber} je zrušená.
                </p>
                <p role="status" className="mt-2 text-sm text-[var(--color-state-warning-fg)]">
                  {planeWarning}
                </p>
                <div className="mt-5 flex justify-end">
                  <button type="button" onClick={onCancelled} className={SECONDARY}>
                    Rozumiem
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
                  Rýchla oprava {versionNumber} ešte nič neurobila. Zruší sa celá aj s rozhovorom so Staviteľom
                  a ďalšia rýchla oprava projektu dostane znova číslo {versionNumber}.
                </p>
                <p className="mt-2 text-xs text-[var(--color-text-muted)]">
                  Keď vznikla z tiketu, tiket sa v Plane vráti do Todo.
                </p>
                <ErrorNote error={error} className="mt-3" />
                <div className="mt-5 flex items-center justify-end gap-2">
                  <button type="button" onClick={() => setAsking(false)} disabled={submitting} className={SECONDARY}>
                    Ponechať
                  </button>
                  <button
                    type="button"
                    onClick={confirm}
                    disabled={submitting}
                    className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {submitting ? "Ruším…" : "Zrušiť rýchlu opravu"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
