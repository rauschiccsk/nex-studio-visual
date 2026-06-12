// Orchestration Cockpit (F-007 §7). Backend owns the pipeline; this board is a
// live view + Director action surface over it.

import { useState } from "react";
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

export default function CockpitPage() {
  const navigate = useNavigate();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);
  const versionId = selectedVersion?.versionId ?? null;

  const { board, error, activity, setBoard } = usePipelineWs(versionId);
  const [inFlight, setInFlight] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

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
        <FolderOpen className="h-10 w-10 text-slate-700" />
        <h2 className="text-sm font-semibold text-slate-300">Nemáš vybranú verziu</h2>
        <p className="max-w-md text-xs text-slate-500">
          Orchestračný cockpit beží nad konkrétnou verziou. Otvor{" "}
          <span className="font-mono">Projects</span> a pripni projekt + verziu.
        </p>
        <button
          onClick={() => navigate("/projects")}
          className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
        >
          Otvoriť Projects
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center justify-between border-b border-slate-800 px-4 py-2">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-semibold text-slate-200">{selectedProject.name}</span>
          <span className="text-slate-600">·</span>
          <span className="font-mono text-xs text-slate-400">{selectedVersion.versionNumber}</span>
        </div>
        <span className="text-[10px] uppercase tracking-wider text-slate-600">
          Orchestrácia
        </span>
      </div>

      {(error || actionError) && (
        <div className="flex-shrink-0 border-b border-red-500/30 bg-red-500/10 px-4 py-2 text-xs text-red-400">
          {actionError ?? error}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* Left rail */}
        <div className="w-56 flex-shrink-0 border-r border-slate-800">
          <PipelineRail state={board?.state ?? null} activeAgent={deriveActiveAgent(board ?? null, activity)} />
        </div>

        {/* Right column */}
        <div className="flex min-w-0 flex-1 flex-col">
          {board && board.state === null ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
              <Play className="h-8 w-8 text-slate-700" />
              <p className="text-xs text-slate-500">Pipeline pre túto verziu ešte nebežala.</p>
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
            <div className="flex flex-1 items-center justify-center text-xs text-slate-600">
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
            <div className="flex w-80 flex-shrink-0 flex-col border-l border-slate-800">
              <TaskPlanPanel versionId={versionId} messages={board?.recent_messages ?? []} />
            </div>
          )}
      </div>
    </div>
  );
}
