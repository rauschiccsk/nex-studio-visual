// ChangeRequestBar — the "Založiť novú verziu z tejto požiadavky" moment in the Riadiace centrum
// (konzultacia-mode.md Part 3; -followup Fix 3/4), mirroring SpecApprovalBar.
//
// Honest-by-construction gate (Fix 3): renders NOTHING unless the LATEST message carries a `change_request`
// marker that has NOT yet been captured AND the version is terminal (`current_stage === 'done'`). So an
// advisory follow-up (no marker) clears the bar, a stale/older marker never re-shows it, it never appears
// mid-build, and it disappears the instant a capture stamps the marker. On click it captures the request into
// a NEW draft version (records a backlog REQ-N + mints the next version in DRAFT, NO build started — backend
// Part 2), then navigates using the RETURNED slug (Fix 4) so the Manažér reviews + starts it deliberately.
// The click is double-submit-guarded synchronously (a ref, not a post-render state race) so a double-click
// can never mint two versions. The consult answer + this marker arrive over the SAME pipeline WS.

import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { GitBranchPlus } from "lucide-react";

import { useActiveContextStore } from "@/store/activeContextStore";
import {
  captureChangeRequestApi,
  type ChangeRequestMarker,
  type PipelineBoard,
} from "@/services/api/pipeline";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
}

export default function ChangeRequestBar({ board, versionId }: Props) {
  const navigate = useNavigate();
  const setSelectedVersion = useActiveContextStore((s) => s.setSelectedVersion);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  // Synchronous double-submit guard (Fix 3): flips BEFORE the first await, so a second click that fires before
  // React re-renders the disabled button is still short-circuited — no duplicate mint from a double-click race.
  const inFlight = useRef(false);

  // Honest-by-construction gate (Fix 3): ONLY the LATEST message, ONLY when it carries an UN-captured
  // change_request marker AND the version is terminal (done/released). Any other latest message (an advisory
  // answer with no marker, an already-captured marker, or a mid-build turn) → null → the bar renders nothing.
  const source = useMemo<{ marker: ChangeRequestMarker; messageId: string } | null>(() => {
    const msgs = board?.recent_messages ?? [];
    const last = msgs[msgs.length - 1];
    const cr = last?.payload?.change_request;
    const terminal = board?.state?.current_stage === "done";
    if (last && cr && cr.summary && !cr.captured_version_id && terminal) {
      return { marker: cr, messageId: last.id };
    }
    return null;
  }, [board?.recent_messages, board?.state?.current_stage]);

  if (!source) return null;

  async function handleCreate() {
    if (inFlight.current) return; // synchronous guard — a concurrent second click is a no-op
    inFlight.current = true;
    setError("");
    setSubmitting(true);
    try {
      const res = await captureChangeRequestApi(versionId, source!.messageId);
      // Pin the new draft version and open it via the RETURNED slug (Fix 4) — correct even if the pin diverged
      // from the consulted version's project. The Manažér reviews + starts the build deliberately (Part 2.3).
      setSelectedVersion({ versionId: res.version_id, versionNumber: res.version_number });
      navigate(`/projects/${res.project_slug}/versions/${res.version_id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Založenie novej verzie zlyhalo.");
      inFlight.current = false;
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-[var(--color-text-secondary)]">
            Táto požiadavka si vyžaduje novú verziu
          </p>
          <p className="mt-0.5 truncate text-[11px] text-[var(--color-text-muted)]">{source.marker.summary}</p>
        </div>
        <button
          type="button"
          onClick={handleCreate}
          disabled={submitting}
          className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <GitBranchPlus className="h-3.5 w-3.5" />
          {submitting ? "Zakladám…" : "Založiť novú verziu z tejto požiadavky"}
        </button>
      </div>

      {error && <p className="text-xs text-[var(--color-status-error)]">{error}</p>}
    </div>
  );
}
