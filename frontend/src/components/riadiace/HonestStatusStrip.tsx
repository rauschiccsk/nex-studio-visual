// HonestStatusStrip — the honest run-status strip pinned above the ConversationThread (spine STEP 1). Reuses
// the proven headerStatus() derivation from the retired AI Agent tab (CUT) + the
// reconnecting/error signals from usePipelineWs. The tone comes from the unified cockpit palette (labels.ts,
// CR-NS-028) — a single source of truth, so "Pozastavené" reads amber and never blue.
//
// INVARIANT (honest, derived, never guessed): the text is derived purely from the live pipeline status. A
// paused / token-stopped run reads "Pozastavené", NOT "working".

import { Loader2 } from "lucide-react";

import type { PipelineState } from "../../services/api/pipeline";
import { PHASE_LABELS, PIPELINE_STATUS_TONE, TONE_BANNER, TONE_DOT } from "../cockpit/labels";
import type { BuildPhase, StatusTone } from "../cockpit/labels";

// Honest status text (salvaged verbatim from the retired AI Agent tab's headerStatus, design §4.4.1):
// Voľný / Pracuje na <projekt> v<ver> — fáza X / Čaká na súhlas / Pozastavené.
function statusText(state: PipelineState | null, projectName: string, versionNumber: string): string {
  if (!state || state.status === "done") return "Voľný";
  if (state.status === "awaiting_manazer" || state.status === "blocked") return "Čaká na súhlas";
  if (state.status === "paused") return "Pozastavené";
  // agent_working — name the project, version, and live phase. version_number is stored without a leading
  // "v" (e.g. "1.0.0"), so prefix it here.
  const phase = PHASE_LABELS[state.current_stage as BuildPhase] ?? state.current_stage;
  const ver = versionNumber ? ` v${versionNumber}` : "";
  return `Pracuje na ${projectName}${ver} — fáza ${phase}`;
}

// Tone from the unified cockpit palette (labels.ts). Idle / done / no state → neutral.
function statusTone(state: PipelineState | null): StatusTone {
  if (!state) return "neutral";
  return PIPELINE_STATUS_TONE[state.status] ?? "neutral";
}

interface Props {
  state: PipelineState | null;
  projectName: string;
  versionNumber: string;
  reconnecting: boolean;
  error: string | null;
}

export function HonestStatusStrip({ state, projectName, versionNumber, reconnecting, error }: Props) {
  const text = statusText(state, projectName, versionNumber);
  const tone = statusTone(state);
  const working = state?.status === "agent_working";

  return (
    <div className="flex flex-shrink-0 flex-col border-b border-[var(--color-border-default)] bg-[var(--color-surface)]">
      <div className="flex items-center gap-2 px-4 py-2">
        <span
          className={`flex min-w-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] ${TONE_BANNER[tone]}`}
        >
          <span className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${TONE_DOT[tone]} ${working ? "animate-pulse" : ""}`} />
          <span className="truncate">{text}</span>
        </span>
      </div>

      {(reconnecting || (error && !reconnecting)) && (
        <div
          className={`flex items-center gap-2 border-t px-4 py-1.5 text-[11px] ${
            reconnecting
              ? "border-[var(--color-state-warning-bg)] bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]"
              : "border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] text-[var(--color-state-error-fg)]"
          }`}
        >
          {reconnecting && <Loader2 className="h-3 w-3 animate-spin" />}
          {reconnecting ? "Spojenie stratené — obnovujem…" : error}
        </div>
      )}
    </div>
  );
}

export default HonestStatusStrip;
