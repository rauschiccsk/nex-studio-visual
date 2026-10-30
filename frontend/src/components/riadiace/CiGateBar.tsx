// CiGateBar — DEV-261: after Programovanie the step to Verifikácia waits for the CI of the commit the cockpit pushed
// after the last task. 30.10.2026 NEX Knowledge 0.2.0 offered „Prejsť na overenie" while its CI still ran; the
// Director: „CI ešte stále beží ale kokpit už ponúka prejsť na overenie."
//
// Honest-by-construction: the backend says what holds the step (`board.ci_gate`) and the bar invents nothing.
//   • pending → „Prejsť na overenie" stands greyed out with the reason; the cockpit offers it by itself once CI is green.
//   • red     → the step is not offered; „Vrátiť agentovi na opravu" (opravit_podla_ci) sends the fix back with what
//               failed — shown only while the backend offers that action.

import { useState } from "react";
import { Hourglass, RotateCw, XCircle } from "lucide-react";

import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

export default function CiGateBar({ board, versionId, onBoard }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  const gate = board?.ci_gate;
  if (!gate) return null;
  const red = gate.state === "red";
  const fixOffered = red && !!board?.available_actions?.includes("opravit_podla_ci");

  async function sendBack() {
    setError(null);
    setSubmitting(true);
    try {
      const nextBoard = await postPipelineActionApi(versionId, { action: "opravit_podla_ci" });
      onBoard(nextBoard);
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Vrátenie na opravu zlyhalo"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="border-t border-[var(--color-border-default)] bg-[var(--color-surface)]">
      <div
        className={
          red
            ? "flex items-center gap-2 border-l-4 border-l-[var(--color-status-error)] bg-[var(--color-state-error-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--color-state-error-fg)]"
            : "flex items-center gap-2 border-l-4 border-l-[var(--color-status-info)] bg-[var(--color-state-info-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--color-state-info-fg)]"
        }
      >
        {red ? (
          <XCircle className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        ) : (
          <Hourglass className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        )}
        <span>{red ? "Kontroly na GitHube zlyhali" : "Čakám na kontroly na GitHube"}</span>
      </div>

      <div className="flex flex-col gap-2 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-[var(--color-text-muted)]">{gate.message}</p>
          {red ? (
            fixOffered && (
              <button
                type="button"
                onClick={sendBack}
                disabled={submitting}
                className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <RotateCw className={`h-3.5 w-3.5 ${submitting ? "animate-spin" : ""}`} aria-hidden="true" />
                {submitting ? "Vraciam…" : "Vrátiť agentovi na opravu"}
              </button>
            )
          ) : (
            <button
              type="button"
              disabled
              title={gate.message}
              className="flex shrink-0 cursor-not-allowed items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white opacity-50"
            >
              Prejsť na overenie
            </button>
          )}
        </div>
        <ErrorNote error={error} />
      </div>
    </div>
  );
}
