// HonestStatusStrip — the honest run-status strip pinned above the ConversationThread (spine STEP 1). Reuses
// the proven headerStatus() derivation from the retired AI Agent tab (CUT) + the
// reconnecting/error signals from usePipelineWs. The tone comes from the unified cockpit palette (labels.ts,
// CR-NS-028) — a single source of truth, so "Pozastavené" reads amber and never blue.
//
// INVARIANT (honest, derived, never guessed): the text is derived purely from the live pipeline status. A
// paused / token-stopped run reads "Pozastavené", NOT "working".

import { Eye, Loader2 } from "lucide-react";

import type { PipelineState } from "../../services/api/pipeline";
import { BLOCK_REASON_LABELS, PHASE_LABELS, PIPELINE_STATUS_TONE, TONE_BANNER, TONE_DOT } from "../cockpit/labels";
import type { BuildPhase, StatusTone } from "../cockpit/labels";

// konzultacia-mode.md Part 3: a TERMINAL version (current_stage === 'done' — a finished / released build) is
// answerable in READ-ONLY advisory mode. The strip shows this so the Manažér knows typing now = advice, not
// a build. No mode toggle — the version's terminal state IS the mode.
const CONSULT_INDICATOR = "Konzultácia — poradím, nič nezmením";

// Honest status text (salvaged verbatim from the retired AI Agent tab's headerStatus, design §4.4.1):
// Voľný / Pracuje na <projekt> v<ver> — fáza X / Čaká na súhlas / Pozastavené.
function statusText(state: PipelineState | null, projectName: string, versionNumber: string): string {
  // Konzultácia (Part 3): a running consult turn on a terminal version reads "premýšľam…" — NOT the generic
  // agent_working "fáza done" (the phase is meaningless in read-only advisory mode). Precedes every branch.
  if (state && state.current_stage === "done" && state.status === "agent_working") {
    return "Konzultácia — premýšľam…";
  }
  // STEP 6 (Hotovo): a signed-off conversation build reads "Hotovo — pripravené na nasadenie" (green via the
  // existing done→green tone). MUST precede the bare-"Voľný" done branch below, else it is shadowed.
  if (state && state.status === "done" && state.mode === "conversation") {
    return "Hotovo — pripravené na nasadenie";
  }
  if (!state || state.status === "done") return "Voľný";
  // Director observation #6: a framework_issue block is an agent → Dedo escalation — there is NOTHING the
  // Manažér can do, so the honest status is "wait for Dedo", NOT the generic "Čaká na súhlas" (which implies
  // the Manažér should act). MUST precede the generic blocked branch below.
  if (state.status === "blocked" && state.block_reason === "framework_issue") {
    return "NEX Studio potrebuje opravu (Dedo) — počkaj";
  }
  // A blocked state names its PRECISE reason (audit Theme 1) — "Systémová chyba" / "Agent sa pýta" / "Treba
  // tvoje rozhodnutie" — never the generic "Čaká na súhlas" that collapsed five distinct situations into one.
  // (framework_issue is handled above; decision_needed keeps its own accurate label.)
  if (state.status === "blocked") {
    return (state.block_reason && BLOCK_REASON_LABELS[state.block_reason]) || "Čaká na súhlas";
  }
  if (state.status === "awaiting_manazer") return "Čaká na súhlas";
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
  // Konzultácia (Part 3): a terminal version (current_stage === 'done') is in read-only advisory mode.
  const consultMode = !!state && state.current_stage === "done";
  // Audit Theme 1: surface the engine's ready-made "čo ďalej" guidance (state.next_action) — previously
  // rendered NOWHERE. Shown for the settled awaiting_manazer wait (blocked states carry it in their own bar:
  // BlockRecoveryBar / Decision Cards / the Dedo banner), so it never double-renders.
  const guidance = state?.status === "awaiting_manazer" ? (state.next_action || "").trim() : "";

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

      {guidance && (
        <div className="flex items-start gap-1.5 border-t border-[var(--color-border-default)] px-4 py-1.5 text-[11px] text-[var(--color-text-muted)]">
          <span className="truncate">{guidance}</span>
        </div>
      )}

      {consultMode && (
        <div className="flex items-center gap-1.5 border-t border-[var(--color-border-default)] px-4 py-1.5 text-[11px] text-[var(--color-text-muted)]">
          <Eye className="h-3 w-3 flex-shrink-0" />
          <span className="truncate">{CONSULT_INDICATOR}</span>
        </div>
      )}

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
