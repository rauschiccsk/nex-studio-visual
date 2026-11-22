// CancelVersion — „Zrušiť verziu“ on the version's page (DEV-491, DEV-492).
//
// 22.11.2026 a fast fix of Orthodox Hermenia was founded by mistake and could not be taken back. DEV-491 put the button
// into the Riadiace centrum; the Director: „Zrušenie verzie nemá byť v Riadiacom centre - nepatrí tam. Má byť vo Verzie
// - Otvoriť.“ A version that did nothing yet — one that never started, or a fast fix still in Príprava with no agent turn
// running — goes away whole: its number is free again and the tickets put into it go back to Todo. The server decides
// whether it takes the cancel (`version.cancellable`) and why not (`version.cancel_refusal`), so a disabled button
// always carries its reason.
import { useState } from "react";
import { Trash2 } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import { cancelVersionApi } from "@/services/api/versions";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import type { Version } from "@/types/version";

interface Props {
  version: Version;
  /** The version is gone — leave its page. */
  onCancelled: () => void;
}

const SECONDARY =
  "rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium " +
  "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-40";

export default function CancelVersion({ version, onCancelled }: Props) {
  const [asking, setAsking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const [planeWarning, setPlaneWarning] = useState<string | null>(null);

  if (version.status === "done" || version.status === "released") return null;
  const cancellable = version.cancellable === true;
  const number = version.version_number;
  const what = version.fast_fix ? `Rýchla oprava ${number}` : `Verzia ${number}`;

  async function confirm() {
    setError(null);
    setSubmitting(true);
    try {
      const result = await cancelVersionApi(version.id);
      if (result.plane_warning) setPlaneWarning(result.plane_warning);
      else onCancelled();
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Zrušenie verzie zlyhalo"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section
      aria-label="Zrušiť verziu"
      className="mt-4 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-[var(--color-text-muted)]">
          Verziu, ktorá ešte nič neurobila, zrušíš celú — jej číslo sa uvoľní a zaradené tikety sa vrátia do Todo.
        </p>
        <button
          type="button"
          onClick={() => setAsking(true)}
          disabled={!cancellable}
          title={cancellable ? undefined : (version.cancel_refusal ?? undefined)}
          className={`flex items-center gap-1.5 ${SECONDARY}`}
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          Zrušiť verziu
        </button>
      </div>

      {asking && (
        <div
          className="fixed inset-0 z-50 flex overflow-y-auto bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="cancel-version-title"
        >
          <div className="m-auto w-full max-w-lg rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-5 shadow-xl">
            <h2 id="cancel-version-title" className="text-sm font-semibold text-[var(--color-text-primary)]">
              Zrušiť verziu {number}
            </h2>
            {planeWarning ? (
              <>
                <p className="mt-3 text-sm text-[var(--color-text-secondary)]">{what} je zrušená.</p>
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
                  {what} ešte nič neurobila. Zruší sa celá a jej číslo {number} sa uvoľní pre ďalšiu verziu.
                </p>
                <p className="mt-2 text-xs text-[var(--color-text-muted)]">
                  Tikety zaradené do verzie sa v Plane vrátia do Todo.
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
                    {submitting ? "Ruším…" : "Zrušiť verziu"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
