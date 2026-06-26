// Orchestration Cockpit (F-007 §7). Backend owns the pipeline; this board is a
// live view + Manažér action surface over it.

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FolderOpen, Loader2, Play } from "lucide-react";

import { useActiveContextStore } from "../store/activeContextStore";
import { usePipelineWs } from "../hooks/usePipelineWs";
import {
  postPipelineActionApi,
  type PipelineActionName,
} from "../services/api/pipeline";
import PipelineRail, { deriveActiveAgent } from "../components/cockpit/PipelineRail";
import ExchangePanel from "../components/cockpit/ExchangePanel";
import DebugTerminalDrawer from "../components/cockpit/DebugTerminalDrawer";
import TaskPlanPanel from "../components/cockpit/TaskPlanPanel";
import { STAGE_LABELS } from "../components/cockpit/labels";

export default function CockpitPage() {
  const navigate = useNavigate();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);
  const versionId = selectedVersion?.versionId ?? null;

  const { board, error, activity, reconnecting, setBoard } = usePipelineWs(versionId);
  const [inFlight, setInFlight] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // CR-2 (v0.7.3): mark the browser tab when the Manažér must act, so a backgrounded "your turn" board is
  // noticeable (a decision-needed state was too subdued — a healthy board read as "stuck"). Capture the app's
  // base title ONCE (ref) and restore it on a non-decision status + on cleanup/unmount, so the "(•) Na rade"
  // marker never leaks to another page.
  const baseTitleRef = useRef(typeof document !== "undefined" ? document.title : "");
  const titleStatus = board?.state?.status ?? null;
  const titleStage = board?.state?.current_stage ?? null;
  useEffect(() => {
    const base = baseTitleRef.current;
    if ((titleStatus === "awaiting_manazer" || titleStatus === "blocked") && titleStage) {
      document.title = `(•) Na rade: Manažér — ${STAGE_LABELS[titleStage]}`;
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
          Orchestračný cockpit beží nad konkrétnou verziou. Otvor{" "}
          <span className="font-mono">Projekty</span> a pripni projekt + verziu.
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
        <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          Orchestrácia
        </span>
      </div>

      {/* A board-load error is suppressed while reconnecting — during a redeploy the snapshot fetch
          ALSO fails, and stacking a red error under the amber "reconnecting" banner is contradictory.
          An actionError (a Manažér action that genuinely failed) always shows. */}
      {(actionError || (error && !reconnecting)) && (
        <div className="flex-shrink-0 border-b border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] px-4 py-2 text-xs text-[var(--color-state-error-fg)]">
          {actionError ?? error}
        </div>
      )}

      {/* Live-connection lost (CR 2026-06-12): the board updates over a WS that auto-reconnects with
          backoff + re-fetches a fresh snapshot on reconnect. Surface the gap so a frozen board is never
          silent — before this a dropped socket (e.g. a backend redeploy) hid the action buttons. */}
      {reconnecting && (
        <div className="flex flex-shrink-0 items-center gap-2 border-b border-[var(--color-state-warning-bg)] bg-[var(--color-state-warning-bg)] px-4 py-2 text-xs text-[var(--color-state-warning-fg)]">
          <Loader2 className="h-3 w-3 animate-spin" />
          Spojenie s orchestrátorom stratené — obnovujem…
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* Left rail */}
        <div className="w-56 flex-shrink-0 border-r border-[var(--color-border-default)]">
          <PipelineRail
            state={board?.state ?? null}
            activeAgent={deriveActiveAgent(board ?? null, activity)}
            agentSessions={board?.agent_sessions}
          />
        </div>

        {/* Right column */}
        <div className="flex min-w-0 flex-1 flex-col">
          {board && board.state === null ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
              <Play className="h-8 w-8 text-[var(--color-text-muted)]" />
              <p className="text-xs text-[var(--color-text-muted)]">Pipeline pre túto verziu ešte nebežala.</p>
              <button
                onClick={() => handleAction("start")}
                disabled={inFlight}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-50"
              >
                {inFlight ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                Spustiť pipeline
              </button>
            </div>
          ) : board ? (
            <ExchangePanel board={board} inFlight={inFlight} activity={activity} onAction={handleAction} />
          ) : (
            <div className="flex flex-1 items-center justify-center text-xs text-[var(--color-text-muted)]">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Načítavam board…
            </div>
          )}

          {board?.state && (
            <DebugTerminalDrawer
              versionId={selectedVersion.versionId}
              currentActor={board.state.current_actor}
            />
          )}
        </div>

        {/* Right column — task-plan tree + per-task audit (F-007 §7, CR-NS-020 CR-5). Only during
            task_plan / build, when the EPIC→FEAT→TASK plan exists and tasks are being built. */}
        {versionId &&
          (board?.state?.current_stage === "task_plan" || board?.state?.current_stage === "build") && (
            <div className="flex w-80 flex-shrink-0 flex-col border-l border-[var(--color-border-default)]">
              <TaskPlanPanel versionId={versionId} messages={board?.recent_messages ?? []} />
            </div>
          )}
      </div>
    </div>
  );
}
