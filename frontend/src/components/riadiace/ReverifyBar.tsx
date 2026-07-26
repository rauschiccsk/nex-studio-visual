// ReverifyBar — the drift re-verify surface ("Over znova", CR-V2-057).
//
// CR-V2-056 reality-anchoring: a version's "overená" (verified) green is computed LIVE from the repo — the
// PASS-bound commit SHA vs current HEAD. When the code moves PAST the verified commit the board reports
// `verified_provenance === "sha_drift"`: the version WAS verified, but the green no longer reflects the current
// code. Until now the cockpit shipped that fact on the board but rendered NOTHING — no warning, no action (a
// drifted `done`/Hotovo version had an EMPTY action set), so a non-expert Manažér would trust a stale green.
// The backend `overit_znovu` handler existed + worked but was never offered anywhere.
//
// This bar closes both halves of the kernel gap: (1) LEGIBILITY — an honest plain-language stale-PASS warning;
// (2) ACTIONABILITY — a one-click "Over znova". audit #8: it serves BOTH drift shapes — a phase build's
// `sha_drift` re-runs the independent Auditor against HEAD; a CONVERSATION build's `hotovo_drift` re-runs its
// own honest self-check and auto re-signs Hotovo on green (Director 2026-07-12). The copy names which happens.
//
// Honest-by-construction (mirrors SchvalitBar): renders NOTHING unless the backend OFFERS `overit_znovu` right
// now — which the board route does ONLY when the live provenance is a DRIFT (`sha_drift` / `hotovo_drift`) AND
// the state is settled (done / awaiting_manazer). So the warning shows exactly when re-verify is meaningful.

import { useState } from "react";
import { RotateCw } from "lucide-react";

import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import WarningActionBar from "@/components/common/WarningActionBar";

interface Props {
  board: PipelineBoard | null;
  versionId: string;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

export default function ReverifyBar({ board, versionId, onBoard }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  // Honest-by-construction gate: the bar exists ONLY when the backend offers `overit_znovu` right now (a
  // settled version whose verified green has drifted past current HEAD).
  if (!board?.available_actions?.includes("overit_znovu")) return null;

  // audit #8: two drift shapes get this button — a phase build re-runs the independent Auditor; a CONVERSATION
  // build re-runs its own honest self-check and, on green, re-signs "Hotovo" automatically. Say which will happen.
  const isHotovoDrift = board.verified_provenance === "hotovo_drift";

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const nextBoard = await postPipelineActionApi(versionId, { action: "overit_znovu" });
      onBoard(nextBoard);
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Opätovné overenie zlyhalo"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    // Honest stale-PASS warning — the green "overená" no longer reflects the current code. Chrome shared
    // with the deploy screen's notice (WarningActionBar) so the two ends of this flow can't drift apart.
    <WarningActionBar
      variant="docked"
      title="Overenie je zastarané — kód sa odvtedy zmenil"
      action={{
        label: submitting ? "Overujem…" : "Over znova",
        icon: RotateCw,
        spinning: submitting,
        disabled: submitting,
        onClick: submit,
      }}
      error={error}
    >
      Táto verzia už bola overená, ale kód sa odvtedy posunul za overený bod — zelené „overená" už nemusí
      platiť.{" "}
      {isHotovoDrift
        ? "„Over znova“ znova čestne prekontroluje aplikáciu proti aktuálnemu kódu; ak je beh v poriadku, verzia sa automaticky znovu označí ako hotová (bez ďalšieho kliku)."
        : "„Over znova“ nechá Audítora zopakovať overenie proti aktuálnemu kódu (bez opravy, bez novej stavby)."}
    </WarningActionBar>
  );
}
