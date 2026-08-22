// PokracovatPoOpraveBar — the way out of a NEX Studio bug (ICCINT-13). A build that escalated a bug in NEX
// Studio ITSELF sits blocked behind NahlasitZnovaBar ("our technical team is on it"). Once the team has
// actually fixed it they release the build from the host, and it lands back here: settled, waiting for the
// Manažér, with ONE thing to do. This bar is that one thing.
//
// Why a separate bar rather than the PlanUlohRail resume rung, which fires the same `pokracovat` verb: that
// rung says "Pokračovať v stavbe" and lives in the task-plan rail, which is a lie in two ways here — the
// build may have stopped in Príprava (no plan, no build), and it was not the manager who paused it. The
// words are the whole value: he needs to know that the thing that broke has been repaired, what was repaired,
// and that pressing this carries on where the work stopped. The rail's rung hides itself while this is up
// (PlanUlohRail reads the same flag), so the screen carries exactly ONE resume button.
//
// Honest-by-construction, like every riadiace bar: renders NOTHING unless the backend BOTH offers
// `pokracovat` right now AND says this build is waiting after a fix.

import { useMemo, useState } from "react";
import { Play, Wrench } from "lucide-react";

import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

// What the technical team said they fixed — the newest `dedo_unblock` message on the feed. Shown verbatim:
// it is the manager's only account of why his build may go on, and paraphrasing it here would drop the one
// fact he cannot get anywhere else. `null` when that message is not in the recent tail.
function fixNote(board: PipelineBoard | null): string | null {
  const msgs = board?.recent_messages ?? [];
  let latest: (typeof msgs)[number] | null = null;
  for (const m of msgs) {
    if (m.payload?.dedo_unblock === true && (!latest || m.seq > latest.seq)) latest = m;
  }
  const text = (latest?.content ?? "").trim();
  return text || null;
}

export default function PokracovatPoOpraveBar({ board, versionId, onBoard }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const note = useMemo(() => fixNote(board), [board]);

  // Honest-by-construction gate. BOTH conditions: the flag alone could survive a board the backend has since
  // moved on from, and `pokracovat` alone is also the ordinary paused-build resume (which the rail owns).
  const offered = !!board?.available_actions?.includes("pokracovat");
  if (!offered || !board?.state?.resume_after_framework_fix) return null;

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const nextBoard = await postPipelineActionApi(versionId, { action: "pokracovat" });
      onBoard(nextBoard);
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Pokračovanie zlyhalo"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="border-t border-[var(--color-border-default)] bg-[var(--color-surface)]">
      {/* Plain-Slovak headline — the repair, not the machinery. Green: the red block above is resolved. */}
      <div className="flex items-center gap-2 border-l-4 border-l-[var(--color-status-success)] bg-[var(--color-state-success-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--color-state-success-fg)]">
        <Wrench className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span>Chybu v NEX Studiu sme opravili — stavba môže pokračovať</span>
      </div>

      <div className="flex flex-col gap-2 px-4 py-3">
        <p className="text-xs text-[var(--color-text-muted)]">
          Náš technický tím opravil chybu, na ktorej sa stavba zastavila. Keď stlačíš „Pokračovať“, AI Agent
          nadviaže tam, kde prestal — nič sa nezačína odznova.
        </p>
        {note && (
          <p className="rounded border border-[var(--color-border-default)] bg-[var(--color-surface-hover)] px-2 py-1 text-xs text-[var(--color-text-secondary)]">
            Čo sa opravilo: {note}
          </p>
        )}
        <div className="flex items-center justify-end">
          <button
            type="button"
            onClick={submit}
            disabled={submitting}
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Play className="h-3.5 w-3.5" />
            {submitting ? "Pokračujem…" : "Pokračovať"}
          </button>
        </div>
        <ErrorNote error={error} />
      </div>
    </div>
  );
}
