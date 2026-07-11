// NahlasitZnovaBar — the framework_issue recovery surface (P0 dead-end fix). When a build blocks with
// block_reason="framework_issue" the bug is in NEX Studio ITSELF, not the manager's project — our technical
// team resolves it. Before this the Manažér saw internal jargon ("Dedo") and had NO clickable action, i.e. a
// locked dead-end. The backend now offers `nahlasit_znova` on such a block; this bar renders the plain-Slovak
// explanation + the manager's ONE concrete move: "Nahlásiť znova" (re-report the bug to the technical team).
// If a framework_issue notification is on the feed it shows WHEN it was reported ("nahlásené o HH:MM").
//
// Honest-by-construction (mirrors the other riadiace bars): renders NOTHING unless the backend currently OFFERS
// `nahlasit_znova` in board.available_actions (which it does only on a framework_issue block).

import { useMemo, useState } from "react";
import { CircleAlert, Send } from "lucide-react";

import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import ErrorNote from "@/components/common/ErrorNote";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

// HH:MM (24h) of the newest framework_issue notification on the feed (payload.framework_issue === true), so the
// Manažér sees the issue was actually reported. `null` when no such message is in the recent tail.
function reportedAt(board: PipelineBoard | null): string | null {
  const msgs = board?.recent_messages ?? [];
  let latest: string | null = null;
  for (const m of msgs) {
    if (m.payload?.framework_issue === true) {
      if (!latest || m.created_at > latest) latest = m.created_at;
    }
  }
  if (!latest) return null;
  const d = new Date(latest);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString("sk-SK", { hour: "2-digit", minute: "2-digit" });
}

export default function NahlasitZnovaBar({ board, versionId, onBoard }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const reported = useMemo(() => reportedAt(board), [board]);

  // Honest-by-construction gate: the bar exists ONLY when the backend offers `nahlasit_znova` right now.
  if (!board?.available_actions?.includes("nahlasit_znova")) return null;

  // The engine's own ready-made "čo ďalej" guidance (already plain Slovak backend-side) — shown when present.
  const guidance = (board.state?.next_action || "").trim();

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const nextBoard = await postPipelineActionApi(versionId, { action: "nahlasit_znova" });
      onBoard(nextBoard);
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Nahlásenie zlyhalo"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="border-t border-[var(--color-border-default)] bg-[var(--color-surface)]">
      {/* Plain-Slovak headline — no "Dedo"/"framework" jargon. */}
      <div className="flex items-center gap-2 border-l-4 border-l-[var(--color-status-error)] bg-[var(--color-state-error-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--color-state-error-fg)]">
        <CircleAlert className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span>NEX Studio má chybu — rieši ju náš technický tím</span>
      </div>

      <div className="flex flex-col gap-2 px-4 py-3">
        <p className="text-xs text-[var(--color-text-muted)]">
          Toto nie je chyba tvojho projektu — ide o chybu v samotnom NEX Studiu. Náš technický tím ju už dostal a
          rieši ju, ty nemusíš robiť nič. Ak sa dlho nič nedeje, môžeš ju nahlásiť znova.
        </p>
        {guidance && <p className="text-xs text-[var(--color-text-muted)]">{guidance}</p>}
        <div className="flex items-center justify-between gap-3">
          <span className="text-[11px] text-[var(--color-text-muted)]">
            {reported ? `nahlásené o ${reported}` : ""}
          </span>
          <button
            type="button"
            onClick={submit}
            disabled={submitting}
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Send className="h-3.5 w-3.5" />
            {submitting ? "Nahlasujem…" : "Nahlásiť znova"}
          </button>
        </div>
        <ErrorNote error={error} />
      </div>
    </div>
  );
}
