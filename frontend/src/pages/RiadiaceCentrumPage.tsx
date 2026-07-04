/**
 * RiadiaceCentrumPage — the Riadiace centrum, the SPINE of NEX Studio v2 (spine STEP 1).
 *
 * Replaces the old Vývoj phase-automaton board + the AI Agent tab with ONE
 * screen: a live 1:1 conversation between the Manažér and the AI partner — exactly how the Director talks to
 * Dedo. A thin, HONEST FE over the backend spine: the conversation IS `board.recent_messages` streamed live
 * over the EXISTING pipeline WS; sends go through the EXISTING single-writer relay. No new WS client, no new
 * mutating call.
 *
 * Layout (CSS grid, single screen, three regions):
 *   - PhaseBar — read-only phase marker across the top of the conversation column.
 *   - ConversationThread — the centre (the only min-h-0 overflow region), with HonestStatusStrip pinned above.
 *   - ConversationComposer — the relay send box at the bottom.
 *   - PlanUlohRail — the right rail (placeholder; the real task-plan lands in step 3, same cell, no churn).
 *
 * Permissions: ``ri`` only (Manažér). Non-ri users see a Lock panel. Project- + version-scoped (follows the
 * pin); the transcript + relay are keyed on the selected version.
 */

import { useNavigate } from "react-router-dom";
import { Lock, FolderOpen } from "lucide-react";

import { useAuthStore } from "@/store/authStore";
import { useActiveContextStore } from "@/store/activeContextStore";
import { usePipelineWs } from "@/hooks/usePipelineWs";
import { relayPipelineMessageApi } from "@/services/api/pipeline";
import ConversationThread from "@/components/riadiace/ConversationThread";
import ConversationComposer from "@/components/riadiace/ConversationComposer";
import SpecApprovalBar from "@/components/riadiace/SpecApprovalBar";
import PhaseBar from "@/components/riadiace/PhaseBar";
import HonestStatusStrip from "@/components/riadiace/HonestStatusStrip";
import PlanUlohRail from "@/components/riadiace/PlanUlohRail";

const PAGE_NAME = "Riadiace centrum";

export default function RiadiaceCentrumPage() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const isDirector = user?.role === "ri";

  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);
  const versionId = selectedVersion?.versionId ?? null;

  // The event-rendered transcript + live activity, streamed over the EXISTING pipeline WS (INVARIANT: no new
  // WS client — live streaming already reaches the FE over this hook).
  const { board, activity, reconnecting, error, setBoard } = usePipelineWs(versionId);

  async function handleRelay(text: string): Promise<{ deferred: boolean }> {
    if (!versionId) throw new Error("Najprv vyber verziu (pin v Projektoch).");
    // The SOLE mutating call — the single-writer relay (Model B): the engine, not this page, writes the turn.
    const res = await relayPipelineMessageApi(versionId, text);
    return { deferred: res.deferred };
  }

  // --- Render guards (salvaged from the retired AI Agent tab) ---

  if (!isDirector) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-[var(--color-canvas)] p-6 text-center">
        <Lock className="h-10 w-10 text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">{PAGE_NAME}</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Riadiace centrum je dostupné iba pre rolu{" "}
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
          Riadiace centrum beží nad konkrétnym projektom. Otvor <span className="font-mono">Projekty</span> a
          pripni projekt (a verziu).
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

  if (!versionId) {
    // Project pinned but no version sub-selection — the transcript + relay are version-scoped.
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-[var(--color-canvas)] p-6 text-center">
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Vyber verziu projektu <span className="font-medium">{selectedProject.name}</span> (pin v Projektoch)
          — Riadiace centrum pracuje a komunikuje nad konkrétnou verziou.
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

  const working = board?.state?.status === "agent_working";

  return (
    <div className="grid h-full grid-cols-[minmax(0,1fr)_320px] grid-rows-[auto_minmax(0,1fr)_auto_auto] bg-[var(--color-canvas)]">
      {/* Top — read-only phase bar (conversation column). */}
      <div className="col-start-1 row-start-1 min-w-0">
        <PhaseBar state={board?.state ?? null} />
      </div>

      {/* Centre — the SPINE: honest status pinned above the live conversation thread (the overflow region). */}
      <div className="col-start-1 row-start-2 flex min-h-0 min-w-0 flex-col">
        <HonestStatusStrip
          state={board?.state ?? null}
          projectName={selectedProject.name}
          versionNumber={selectedVersion?.versionNumber ?? ""}
          reconnecting={reconnecting}
          error={error}
        />
        <ConversationThread messages={board?.recent_messages ?? []} activity={activity} working={!!working} />
      </div>

      {/* Approval moment — the "Schváliť Špecifikáciu" bar (STEP 2). Renders null unless the backend
          currently offers ``approve_spec``; sits between the thread and the relay send box. */}
      <div className="col-start-1 row-start-3 min-w-0">
        <SpecApprovalBar board={board} versionId={versionId} onBoard={setBoard} />
      </div>

      {/* Bottom — the relay send box. */}
      <div className="col-start-1 row-start-4 min-w-0">
        <ConversationComposer onRelay={handleRelay} disabled={!versionId} />
      </div>

      {/* Right rail — Plán úloh placeholder, spanning the full height. */}
      <div className="col-start-2 row-start-1 row-span-4 min-h-0">
        <PlanUlohRail versionId={versionId} />
      </div>
    </div>
  );
}
