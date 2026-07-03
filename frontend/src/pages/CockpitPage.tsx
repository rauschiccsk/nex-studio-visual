// 🔄 Vývoj — the build board (CR-V2-021, design §4.4.2). Version-scoped board for the selected version: a
// horizontal 4-phase bar at the TOP whose chips ARE the tabs, permanent per-phase content (durable after the
// build completes), who's-up status, schvaľovacie body buttons (dial-governed), and a raw-terminal peek
// drawer. The backend owns the pipeline; this board is a live view + Manažér action surface over it.

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FolderOpen, Loader2, Play } from "lucide-react";

import { useActiveContextStore } from "../store/activeContextStore";
import { usePipelineWs } from "../hooks/usePipelineWs";
import { postPipelineActionApi, type PipelineActionName } from "../services/api/pipeline";
import PipelineRail, { deriveActiveAgent, WhosUp } from "../components/cockpit/PipelineRail";
import ExchangePanel from "../components/cockpit/ExchangePanel";
import DecisionCardStack from "../components/cockpit/DecisionCardStack";
import PipelineActionBar from "../components/cockpit/PipelineActionBar";
import DebugTerminalDrawer from "../components/cockpit/DebugTerminalDrawer";
import TaskPlanPanel from "../components/cockpit/TaskPlanPanel";
import { PHASE_LABELS, type BuildPhase } from "../components/cockpit/labels";

export default function CockpitPage() {
  const navigate = useNavigate();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);
  const versionId = selectedVersion?.versionId ?? null;

  const { board, error, activity, reconnecting, setBoard } = usePipelineWs(versionId);
  const [inFlight, setInFlight] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // The phase tab the Manažér is VIEWING. Two coexisting states on the bar (design §4.4.2): ● = where the
  // build IS (state.current_stage, auto-advances); highlighted = the viewed tab. They can differ — the
  // Manažér may review a finished Návrh while the build runs ahead in Programovanie. The viewed tab follows
  // the build position UNLESS the Manažér has clicked a specific tab (manualViewed sticks to their choice
  // until the build position changes again).
  const [manualViewed, setManualViewed] = useState<BuildPhase | null>(null);
  const buildPhase = (board?.state?.current_stage as BuildPhase | undefined) ?? "priprava";
  const lastBuildPhaseRef = useRef<BuildPhase>(buildPhase);
  useEffect(() => {
    // When the build position auto-advances, snap the viewed tab to it (clear the manual override).
    if (lastBuildPhaseRef.current !== buildPhase) {
      lastBuildPhaseRef.current = buildPhase;
      setManualViewed(null);
    }
  }, [buildPhase]);
  // Hotovo (terminal) is not a tab — clamp the viewed tab to Verifikácia when the build position is done.
  const viewedPhase: BuildPhase = manualViewed ?? (buildPhase === "done" ? "verifikacia" : buildPhase);

  // CR-2 (v0.7.3): mark the browser tab when the Manažér must act, so a backgrounded "your turn" board is
  // noticeable. Capture the base title ONCE and restore on a non-decision status + on cleanup.
  const baseTitleRef = useRef(typeof document !== "undefined" ? document.title : "");
  const titleStatus = board?.state?.status ?? null;
  const titleStage = board?.state?.current_stage ?? null;
  useEffect(() => {
    const base = baseTitleRef.current;
    if ((titleStatus === "awaiting_manazer" || titleStatus === "blocked") && titleStage) {
      document.title = `(•) Na rade: Manažér — ${PHASE_LABELS[titleStage as BuildPhase] ?? titleStage}`;
    } else {
      document.title = base;
    }
    return () => {
      document.title = base;
    };
  }, [titleStatus, titleStage]);

  const handleAction = async (action: PipelineActionName, payload?: Record<string, unknown>) => {
    if (!versionId) return;
    setInFlight(true);
    setActionError(null);
    try {
      const fresh = await postPipelineActionApi(versionId, { action, payload });
      setBoard(fresh);
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : "Akcia zlyhala");
    } finally {
      setInFlight(false);
    }
  };

  // State A — no project/version pinned.
  if (!selectedProject || !selectedVersion) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
        <FolderOpen className="h-10 w-10 text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Nemáš vybranú verziu</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Vývoj beží nad konkrétnou verziou. Otvor <span className="font-mono">Projekty</span> a pripni
          projekt + verziu.
        </p>
        <button
          onClick={() => navigate("/projects")}
          className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
        >
          Otvoriť Projekty
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center justify-between border-b border-[var(--color-border-default)] px-4 py-2">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-semibold text-[var(--color-text-primary)]">{selectedProject.name}</span>
          <span className="text-[var(--color-text-muted)]">·</span>
          <span className="font-mono text-xs text-[var(--color-text-secondary)]">{selectedVersion.versionNumber}</span>
        </div>
        <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Vývoj</span>
      </div>

      {(actionError || (error && !reconnecting)) && (
        <div className="flex-shrink-0 border-b border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] px-4 py-2 text-xs text-[var(--color-state-error-fg)]">
          {actionError ?? error}
        </div>
      )}

      {reconnecting && (
        <div className="flex flex-shrink-0 items-center gap-2 border-b border-[var(--color-state-warning-bg)] bg-[var(--color-state-warning-bg)] px-4 py-2 text-xs text-[var(--color-state-warning-fg)]">
          <Loader2 className="h-3 w-3 animate-spin" />
          Spojenie s orchestrátorom stratené — obnovujem…
        </div>
      )}

      {board && board.state === null ? (
        // The pipeline never ran — Zadanie + "Spustiť" live on the version's page (Director's IA).
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <Play className="h-8 w-8 text-[var(--color-text-muted)]" />
          <p className="max-w-md text-xs text-[var(--color-text-muted)]">
            Vývoj tejto verzie ešte nezačal. Zadanie zadáš a tvorbu špecifikácie spustíš na stránke verzie.
          </p>
          <button
            onClick={() => navigate(`/projects/${selectedProject.slug}/versions/${selectedVersion.versionId}`)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
          >
            Otvoriť verziu
          </button>
        </div>
      ) : board ? (
        <>
          {/* Horizontal 4-phase bar (chips = tabs) */}
          <PipelineRail state={board.state} viewedPhase={viewedPhase} onSelectPhase={setManualViewed} />

          {/* Who's-up status (below the tabs) */}
          <WhosUp
            state={board.state}
            activeAgent={deriveActiveAgent(board, activity)}
            agentSessions={board.agent_sessions}
            currentTask={board.current_task}
            verifiedProvenance={board.verified_provenance}
          />

          {/* CR-V2-041: the interactive consultation — pinned ABOVE the phase content when the build is
              blocked on a decision. The Decision Cards ARE the action surface (one decision at a time);
              the buttons below are suppressed to {decide, ask} by the backend's available_actions. */}
          {board.state?.block_reason === "decision_needed" && (
            <DecisionCardStack
              messages={board.recent_messages}
              onDecide={(p) => handleAction("decide", p as unknown as Record<string, unknown>)}
              inFlight={inFlight}
            />
          )}

          {/* The viewed phase's permanent content */}
          <div className="flex min-h-0 flex-1 flex-col">
            <ExchangePanel
              board={board}
              viewedPhase={viewedPhase}
              activity={activity}
              taskPlanSlot={
                // The interactive task-plan tree lives in the Návrh tab (as the last part of the design
                // doc) AND drives the Programovanie split view (CR-V2-023, design §4.5). One panel
                // instance per render; ExchangePanel places it per phase.
                versionId && (viewedPhase === "navrh" || viewedPhase === "programovanie") ? (
                  <TaskPlanPanel versionId={versionId} messages={board.recent_messages} />
                ) : undefined
              }
            />
          </div>

          {/* Schvaľovacie body — action buttons */}
          <div className="flex-shrink-0 border-t border-[var(--color-border-default)] p-3">
            <PipelineActionBar
              state={board.state}
              availableActions={board.available_actions}
              allTasksDone={board.all_tasks_done}
              buildOpenFindings={board.build_open_findings}
              inFlight={inFlight}
              onAction={handleAction}
            />
          </div>

          {/* Raw-terminal peek drawer */}
          {board.state && (
            <DebugTerminalDrawer versionId={selectedVersion.versionId} currentActor={board.state.current_actor} />
          )}
        </>
      ) : (
        <div className="flex flex-1 items-center justify-center text-xs text-[var(--color-text-muted)]">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Načítavam board…
        </div>
      )}
    </div>
  );
}
