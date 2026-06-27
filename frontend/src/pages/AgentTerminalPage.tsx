/**
 * AgentTerminalPage — the 👨‍💻 AI Agent tab (CR-V2-022, design §4.4.1).
 *
 * The intimate console where the Manažér watches and talks to the v2 doer (the single AI Agent) live.
 *
 * CRITICAL (E-critic F-risk-2): the live view is an EVENT-RENDERED TRANSCRIPT built from the engine's
 * stream-json broadcast over the pipeline WS (durable ``recent_messages`` + ephemeral live ``activity``
 * lines), NOT a reuse of the v1 xterm raw-ANSI byte model. The raw xterm survives ONLY as the break-glass
 * debug PTY — a separate render path owned by :file:`components/PersistentTerminalsLayer.tsx`, revealed on
 * demand via the break-glass toggle (store ``breakGlassOpen``); the two are deliberately not conflated.
 *
 * Layout, top to bottom (design §4.4.1):
 *   - Header — name "AI Agent" + status: Voľný / Pracuje na <projekt> v<ver> — fáza X / Čaká na súhlas.
 *   - Thin 4-phase strip — a compact mirror of the Vývoj phase bar (→ links to 🔄 Vývoj).
 *   - Live activity console — the event-rendered transcript (the AI Agent session, rendered, not raw bytes).
 *   - Helpers panel — "+ N pomocníci" (fed by the CR-V2-018 helpers WS frame; hidden when none active).
 *   - Input box — relays the Manažér's message through the engine (POST /relay), Model B single-writer.
 *
 * Behaviour: Idle → ad-hoc consultation; Building → watch live + answer schvaľovacie body inline (also
 * flagged by the "čaká na Manažéra" badge in 🔄 Vývoj). Project-scoped: follows the pin (and the version
 * sub-selection, which the relay/transcript are keyed on).
 *
 * Permissions: ``ri`` only (Director / Manažér). Non-ri users see a Lock panel.
 */

import { useNavigate } from "react-router-dom";
import { Lock, Loader2, FolderOpen, Terminal, X } from "lucide-react";

import { useAuthStore } from "@/store/authStore";
import { useActiveContextStore } from "@/store/activeContextStore";
import { useAgentTerminalStore } from "@/store/agentTerminalStore";
import { usePipelineWs } from "@/hooks/usePipelineWs";
import { relayPipelineMessageApi, type PipelineState } from "@/services/api/pipeline";
import type { AgentRole } from "@/services/api/agentTerminal";
import { PHASE_LABELS, type BuildPhase } from "@/components/cockpit/labels";
import AgentTranscript from "@/components/agent/AgentTranscript";
import AgentHelpersPanel from "@/components/agent/AgentHelpersPanel";
import AgentPhaseStrip from "@/components/agent/AgentPhaseStrip";
import AgentInputBox from "@/components/agent/AgentInputBox";

const AGENT_NAME = "AI Agent";

// Header status (design §4.4.1): Voľný / Pracuje na <projekt> v<ver> — fáza X / Čaká na súhlas. Derived
// honestly from the live pipeline state — never guessed from raw terminal text.
type HeaderTone = "idle" | "working" | "waiting";
interface HeaderStatus {
  text: string;
  tone: HeaderTone;
}

function headerStatus(state: PipelineState | null, projectName: string, versionNumber: string): HeaderStatus {
  if (!state || state.status === "done") return { text: "Voľný", tone: "idle" };
  if (state.status === "awaiting_manazer" || state.status === "blocked")
    return { text: "Čaká na súhlas", tone: "waiting" };
  if (state.status === "paused") return { text: "Pozastavené", tone: "waiting" };
  // agent_working — name the project, version, and live phase (design §4.4.1: "Pracuje na <projekt> v<ver>
  // — fáza X"). version_number is stored without a leading "v" (e.g. "1.0.0"), so prefix it here.
  const phase = PHASE_LABELS[state.current_stage as BuildPhase] ?? state.current_stage;
  const ver = versionNumber ? ` v${versionNumber}` : "";
  return { text: `Pracuje na ${projectName}${ver} — fáza ${phase}`, tone: "working" };
}

const TONE_CHIP: Record<HeaderTone, string> = {
  idle: "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]",
  working: "bg-[var(--color-state-info-bg)] text-[var(--color-state-info-fg)]",
  waiting: "bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]",
};
const TONE_DOT: Record<HeaderTone, string> = {
  idle: "bg-[var(--color-text-muted)]",
  working: "bg-[var(--color-status-info)] animate-pulse",
  waiting: "bg-[var(--color-status-warning)]",
};

export interface AgentTerminalPageProps {
  role: AgentRole;
}

export default function AgentTerminalPage({ role }: AgentTerminalPageProps) {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const isDirector = user?.role === "ri";

  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);
  const versionId = selectedVersion?.versionId ?? null;

  // The event-rendered transcript + helpers feed + write_rejected signal, live over the pipeline WS.
  const { board, activity, helpers, writeRejected, clearWriteRejected, reconnecting, error } =
    usePipelineWs(versionId);

  // Break-glass raw-PTY console (the v1 xterm, kept for debug only) — owned by the store + the layer.
  const slot = useAgentTerminalStore((s) => s[role]);
  const breakGlassOpen = useAgentTerminalStore((s) => s.breakGlassOpen);
  const setBreakGlassOpen = useAgentTerminalStore((s) => s.setBreakGlassOpen);
  const spawn = useAgentTerminalStore((s) => s.spawn);
  const debugSession = slot.session;
  const debugSpawning = slot.status === "spawning";

  async function handleRelay(text: string): Promise<{ deferred: boolean }> {
    if (!versionId) throw new Error("Najprv vyber verziu (pin v Projektoch).");
    const res = await relayPipelineMessageApi(versionId, text);
    return { deferred: res.deferred };
  }

  // Break-glass: ensure a raw PTY exists, then reveal it. The store + layer keep it alive across navigation.
  async function openBreakGlass() {
    if (!selectedProject) return;
    if (!debugSession) await spawn(role, selectedProject.slug);
    setBreakGlassOpen(true);
  }

  // --- Render guards ---

  if (!isDirector) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-[var(--color-canvas)] p-6 text-center">
        <Lock className="h-10 w-10 text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">{AGENT_NAME}</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          AI Agent tab je dostupný iba pre rolu{" "}
          <code className="rounded bg-[var(--color-surface)] px-1 py-0.5">ri</code> (Manažér).
        </p>
      </div>
    );
  }

  if (!selectedProject) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 bg-[var(--color-canvas)] p-6 text-center">
        <FolderOpen className="h-10 w-10 text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Nemáš vybraný projekt</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          AI Agent beží nad konkrétnym projektom. Otvor <span className="font-mono">Projekty</span> a pripni
          projekt (a verziu).
        </p>
        <button
          onClick={() => navigate("/projects")}
          className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
        >
          → Otvor Projekty
        </button>
      </div>
    );
  }

  const status = headerStatus(board?.state ?? null, selectedProject.name, selectedVersion?.versionNumber ?? "");
  const working = board?.state?.status === "agent_working";

  return (
    // When the break-glass console is open the raw xterm viewport bleeds through from
    // PersistentTerminalsLayer at z-0 — keep the page background TRANSPARENT so it shows (the header + strip
    // carry their own opaque surface). When closed, the event-rendered transcript owns the canvas.
    <div className={`relative z-10 flex h-full flex-col ${breakGlassOpen ? "" : "bg-[var(--color-canvas)]"}`}>
      {/* Header — name + live status. relative z-10 so it sits above the break-glass xterm layer (z-0). */}
      <div className="flex flex-shrink-0 items-center justify-between gap-3 border-b border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-3">
          <h1 className="text-sm font-semibold text-[var(--color-text-primary)]">👨‍💻 {AGENT_NAME}</h1>
          <span
            className={`flex items-center gap-1.5 truncate rounded-full px-2 py-0.5 text-[11px] ${TONE_CHIP[status.tone]}`}
          >
            <span className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${TONE_DOT[status.tone]}`} />
            <span className="truncate">{status.text}</span>
          </span>
        </div>
        <button
          onClick={() => (breakGlassOpen ? setBreakGlassOpen(false) : void openBreakGlass())}
          disabled={debugSpawning}
          title="Surový terminál (break-glass) — pre ladenie"
          className="flex flex-shrink-0 items-center gap-1 rounded border border-[var(--color-border-default)] px-2 py-0.5 text-[11px] text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:opacity-40"
        >
          {debugSpawning ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : breakGlassOpen ? (
            <X className="h-3 w-3" />
          ) : (
            <Terminal className="h-3 w-3" />
          )}
          {breakGlassOpen ? "Zavrieť terminál" : "Surový terminál"}
        </button>
      </div>

      {/* Thin 4-phase strip → Vývoj. */}
      <AgentPhaseStrip state={board?.state ?? null} />

      {(error && !reconnecting) || reconnecting ? (
        <div
          className={`flex flex-shrink-0 items-center gap-2 border-b px-4 py-1.5 text-[11px] ${
            reconnecting
              ? "border-[var(--color-state-warning-bg)] bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]"
              : "border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] text-[var(--color-state-error-fg)]"
          }`}
        >
          {reconnecting && <Loader2 className="h-3 w-3 animate-spin" />}
          {reconnecting ? "Spojenie stratené — obnovujem…" : error}
        </div>
      ) : null}

      {/* Body — event-rendered transcript (the break-glass raw xterm, when open, shows through from the
          layer at z-0; this body is then a transparent passthrough). */}
      {breakGlassOpen ? (
        // The raw PTY viewport bleeds through from PersistentTerminalsLayer at z-0; keep the body empty so it
        // shows. The header + strip above (z-10) stay visible on top.
        <div className="min-h-0 flex-1" />
      ) : !versionId ? (
        // Project pinned but no version sub-selection — the transcript + relay are version-scoped.
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <p className="max-w-md text-xs text-[var(--color-text-muted)]">
            Vyber verziu projektu <span className="font-medium">{selectedProject.name}</span> (pin v
            Projektoch) — AI Agent pracuje a komunikuje nad konkrétnou verziou.
          </p>
          <button
            onClick={() => navigate("/projects")}
            className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
          >
            → Otvor Projekty
          </button>
        </div>
      ) : (
        <AgentTranscript messages={board?.recent_messages ?? []} activity={activity} working={!!working} />
      )}

      {/* Helpers panel — hidden when none active. */}
      {!breakGlassOpen && <AgentHelpersPanel helpers={helpers} />}

      {/* Relay input box — POSTs to the engine relay (Model B single-writer); handles deferred + the raw-PTY
          write_rejected hint. Disabled until a version is pinned (the relay is version-scoped). */}
      {!breakGlassOpen && (
        <AgentInputBox
          onRelay={handleRelay}
          writeRejected={writeRejected}
          onClearWriteRejected={clearWriteRejected}
          disabled={!versionId}
        />
      )}
    </div>
  );
}
