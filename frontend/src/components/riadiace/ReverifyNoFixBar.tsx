// ReverifyNoFixBar — the "re-verify without a project fix" exit from a Verifikácia fix-loop (overit_bez_opravy,
// v4.0.10, Director 2026-07-20).
//
// A Verifikácia FAIL routes a targeted fix task back to the AI Agent (the FIXER). But when the ROOT CAUSE was
// fixed OUTSIDE the project — the NEX Studio engine / framework / infra, not the app's own code — that fix task
// has NOTHING to change in the project. The per-task self-check demands a commit, so the loop either wedges or
// PRESSURES the agent into a spurious patch of the test project (the §15 "fix NEX Studio, not the project"
// violation). This bar is the Manažér's clean, honest exit: SKIP the fix and re-run the Verifikácia gate
// directly (release-acceptance + the independent Auditor against HEAD). On PASS the version settles for the
// end sign-off (Schváliť → Hotovo); on FAIL the normal targeted fix loop resumes.
//
// Honest-by-construction (mirrors ReverifyBar / SchvalitBar): renders NOTHING unless the backend OFFERS
// `overit_bez_opravy` right now — which the board route does ONLY inside a Verifikácia-originated fix-loop
// (a Programovanie state, blocked/paused, with a Verifikácia fix-scope on record). So the button shows exactly
// when re-verifying-without-a-fix is meaningful.

import { useState } from "react";
import { ShieldCheck, RotateCw } from "lucide-react";

import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

export default function ReverifyNoFixBar({ board, versionId, onBoard }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  // Honest-by-construction gate: the bar exists ONLY when the backend offers `overit_bez_opravy` right now.
  if (!board?.available_actions?.includes("overit_bez_opravy")) return null;

  // Two situations reach this action (v4.0.49); show the RIGHT plain-language label for each so the manager
  // is never told "chyba bola mimo projektu" when the real reason is a stalled check.
  //   • verifikacia block  → the Auditor's OWN turn stalled (timed out); the app already passed the smoke.
  //   • programovanie loop → the root cause was fixed OUTSIDE the project; skip the fix, re-run the gate.
  const isVerifStall = board.state?.current_stage === "verifikacia";
  const heading = isVerifStall
    ? "Overenie sa zaseklo — spusti ho znova"
    : "Chyba bola mimo projektu? Znova over bez opravy";
  const explanation = isVerifStall
    ? "Predchádzajúci overovací beh sa zasekol (časový limit) — nie je to chyba aplikácie, tá prešla skúškou po spustení. „Znova spustiť overenie“ zopakuje koncové overenie (spustí aplikáciu a nezávislý Audítor ju posúdi). Ak prejde, verzia je pripravená na schválenie (Hotovo)."
    : "Ak sa blokujúca chyba vyriešila mimo tohto projektu (v samotnom NEX Studiu), v projekte niet čo opravovať. „Znova overiť“ preskočí opravnú úlohu a rovno zopakuje koncové overenie (spustí aplikáciu a nezávislý Audítor ju posúdi). Ak prejde, verzia je pripravená na schválenie (Hotovo).";
  const buttonLabel = isVerifStall ? "Znova spustiť overenie" : "Znova overiť bez opravy";

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const nextBoard = await postPipelineActionApi(versionId, { action: "overit_bez_opravy" });
      onBoard(nextBoard);
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Opätovné overenie zlyhalo"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="border-t border-[var(--color-border-default)] bg-[var(--color-surface)]">
      <div className="flex items-center gap-2 border-l-4 border-l-[var(--color-status-info)] bg-[var(--color-state-info-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--color-state-info-fg)]">
        <ShieldCheck className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span>{heading}</span>
      </div>

      <div className="flex flex-col gap-2 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-[var(--color-text-muted)]">{explanation}</p>
          <button
            type="button"
            onClick={submit}
            disabled={submitting}
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RotateCw className={`h-3.5 w-3.5 ${submitting ? "animate-spin" : ""}`} aria-hidden="true" />
            {submitting ? "Overujem…" : buttonLabel}
          </button>
        </div>
        <ErrorNote error={error} />
      </div>
    </div>
  );
}
