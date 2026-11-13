// ReturnToPripravaBar — „Vrátiť do Prípravy“: a version in Návrh or Vizuál goes back to Príprava (DEV-367).
//
// NEX Finance 0.1.0 reached Vizuál and the whole design was wrong; the screen offered only the rework of the phase it
// was in. Director 12.11.2026: „Súhlasím, vrátime sa do prípravy, pretože celý projekt je zlý.“
//
// Honest-by-construction: drawn only while the engine offers the action (a regular version settled in Návrh or
// Vizuál, Programovanie not started). It throws work away, so the first click sends nothing — it says what happens
// and asks again; only „Naozaj vrátiť do Prípravy“ sends it. The question stays open until the engine stops offering
// the action — after the return the board is in Príprava and the bar goes, and with it the question (the next offer,
// or another version, starts from the plain button).

import { useState } from "react";
import { Undo2 } from "lucide-react";

import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { actionLabel } from "@/components/controlCenter/actionLabel";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

const BUTTON =
  "flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-50";
const CONFIRM_BUTTON =
  "shrink-0 rounded-lg bg-[var(--color-status-error)] px-4 py-1.5 text-xs font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";

export default function ReturnToPripravaBar({ board, versionId, onBoard }: Props) {
  // Honest-by-construction gate. The question lives in the inner part, so it goes with the offer and the version.
  if (!board?.available_actions?.includes("vratit_do_pripravy")) return null;
  return (
    <ReturnToPriprava
      key={versionId}
      versionId={versionId}
      onBoard={onBoard}
      label={actionLabel(board, "vratit_do_pripravy")}
    />
  );
}

function ReturnToPriprava({ versionId, onBoard, label }: Omit<Props, "board"> & { label: string }) {
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      onBoard(await postPipelineActionApi(versionId, { action: "vratit_do_pripravy" }));
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Vrátenie do Prípravy zlyhalo"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-3">
      {confirming ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-[var(--color-text-primary)]">
            Návrh, plán úloh a Vizuál sa zahodia — ostanú v histórii gitu. Špecifikácia sa znova otvorí a verzia
            sa vráti do Prípravy, kde ju so Staviteľom prepracuješ.
          </p>
          <div className="flex shrink-0 items-center gap-2">
            <button type="button" onClick={() => setConfirming(false)} disabled={submitting} className={BUTTON}>
              Nevracať
            </button>
            <button type="button" onClick={submit} disabled={submitting} className={CONFIRM_BUTTON}>
              {submitting ? "Vraciam…" : "Naozaj vrátiť do Prípravy"}
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-[var(--color-text-muted)]">
            Je celý návrh zlý? Verziu môžeš vrátiť do Prípravy a Špecifikáciu prepracovať od základu.
          </p>
          <button type="button" onClick={() => setConfirming(true)} className={BUTTON}>
            <Undo2 className="h-3.5 w-3.5" aria-hidden="true" />
            {label}
          </button>
        </div>
      )}
      <ErrorNote error={error} />
    </div>
  );
}
