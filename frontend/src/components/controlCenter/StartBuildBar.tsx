// StartBuildBar — the start of a version that has not started yet (DEV-351).
//
// 10.11.2026 the Director founded NEX Knowledge 1.1.0, assigned its requirement from the Zásobník and opened
// Riadiace centrum to begin: „kde mám stlačiť začatie tvorby špecifikácie, mám prázdne okno.“ The start lived only on
// the version's page, and the first message typed here would have started the version as a conversation, without the
// five phases. This bar is the start where the work happens; the message box stays locked until it is pressed.
//
// Honest-by-construction, like every riadiace bar: renders NOTHING unless the board says the version has no build yet
// AND offers `start`.

import { useState } from "react";
import { Play } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns. */
  onBoard: (board: PipelineBoard) => void;
}

export default function StartBuildBar({ board, versionId, onBoard }: Props) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  if (!board || board.state || !board.available_actions?.includes("start")) return null;

  async function start() {
    setStarting(true);
    setError(null);
    try {
      onBoard(await postPipelineActionApi(versionId, { action: "start" }));
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Tvorbu špecifikácie sa nepodarilo spustiť"));
      setStarting(false);
    }
  }

  return (
    <div
      role="region"
      aria-label="Spustenie verzie"
      className="rounded-lg border border-[var(--color-accent-primary)] bg-[var(--color-surface)] px-4 py-3"
    >
      <p className="text-sm font-medium text-[var(--color-text-primary)]">Verzia ešte nebeží.</p>
      <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
        Tvorba špecifikácie začne Prípravou: Staviteľ prečíta Zadanie verzie aj
        požiadavky, ktoré si k nej priradil zo Zásobníka.
      </p>
      <div className="mt-3 flex justify-end">
        <button
          type="button"
          onClick={() => void start()}
          disabled={starting}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Play className="h-3.5 w-3.5" aria-hidden="true" />
          {starting ? "Spúšťam…" : "Spustiť tvorbu špecifikácie"}
        </button>
      </div>
      <ErrorNote error={error} className="mt-2" />
    </div>
  );
}
