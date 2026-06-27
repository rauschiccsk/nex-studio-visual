// Per-phase content panel for the Vývoj board (CR-V2-021, design §4.4.2). The phase chips ARE the tabs;
// this renders the VIEWED phase's durable artifact + (when viewing the live phase) the status banner +
// activity feed. Permanent content persists after the build completes — a finished phase stays viewable.
//
//   Príprava      → Špecifikácia .md (the manager's reading view)
//   Návrh         → the design document incl. the task plan
//   Programovanie → split view: coding activity LEFT + the task plan RIGHT (the task-plan slot is owned by
//                   CR-V2-023; this panel hosts it)
//   Verifikácia   → the Auditor's verdict + findings

import { useEffect, useRef, type ReactNode } from "react";
import { Bell } from "lucide-react";

import type { ActivityLine, PipelineBoard, PipelineState } from "../../services/api/pipeline";
import type { BuildPhase } from "./labels";
import PipelineActivityFeed from "./PipelineActivityFeed";
import { PhaseArtifact } from "./PhaseArtifact";
import { DECISION_BANNER, PHASE_LABELS, PIPELINE_STATUS_TONE, TONE_BANNER } from "./labels";

// Slovak placeholder per phase tab when it has no artifact yet.
const PHASE_PLACEHOLDER: Record<BuildPhase, string> = {
  priprava: "Špecifikácia ešte nie je pripravená. Spusti tvorbu špecifikácie.",
  navrh: "Návrhový dokument ešte nevznikol. Najprv schváľ špecifikáciu.",
  programovanie: "Programovanie ešte nezačalo.",
  verifikacia: "Auditor ešte nevydal verdikt.",
  done: "Hotovo.",
};

// The Slovak banner for the live phase (only shown when the viewed tab IS the build position).
function bannerText(state: PipelineState): string {
  const phase = PHASE_LABELS[state.current_stage as BuildPhase] ?? state.current_stage;
  switch (state.status) {
    case "agent_working":
      return `Prebieha fáza ${phase}`;
    case "awaiting_manazer":
      return `Na rade: Manažér — posúď fázu ${phase}`;
    case "blocked":
      return state.block_reason === "agent_question"
        ? "Na rade: Manažér — odpovedz AI Agentovi"
        : `Fáza ${phase} blokovaná — skús znova`;
    case "paused":
      return "Programovanie pozastavené — pokračuj alebo uprav";
    case "done":
      return "Hotovo";
    default:
      return phase;
  }
}

interface Props {
  board: PipelineBoard;
  /** Which phase tab the Manažér is viewing (the highlighted chip). */
  viewedPhase: BuildPhase;
  activity: ActivityLine[];
  /** The task-plan slot for the Programovanie split view (CR-V2-023 supplies the panel). */
  taskPlanSlot?: ReactNode;
}

export function ExchangePanel({ board, viewedPhase, activity, taskPlanSlot }: Props) {
  const { state, recent_messages } = board;
  const feedRef = useRef<HTMLDivElement>(null);

  // The viewed tab is the build's CURRENT phase (the live one) when it matches current_stage.
  const isLivePhase = state != null && (state.current_stage as BuildPhase) === viewedPhase;

  useEffect(() => {
    feedRef.current?.scrollTo?.({ top: feedRef.current.scrollHeight });
  }, [recent_messages.length]);

  const tone = state ? PIPELINE_STATUS_TONE[state.status] ?? "neutral" : "neutral";
  const decisionNeeded = isLivePhase && (state?.status === "awaiting_manazer" || state?.status === "blocked");
  const decisionBanner = state ? DECISION_BANNER[tone] : undefined;

  // Programovanie split view (design §4.5): coding activity LEFT + task plan RIGHT.
  const programovanieSplit = viewedPhase === "programovanie";

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* The live-phase status banner — only when the viewed tab IS the build position (else a finished
          tab is a quiet durable record; the live banner belongs to the live phase). */}
      {isLivePhase &&
        state &&
        (decisionNeeded && decisionBanner ? (
          <div
            className={`flex flex-shrink-0 items-center gap-2 border-l-4 px-4 py-2.5 text-sm font-semibold ${decisionBanner}`}
          >
            <Bell className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
            <span>{bannerText(state)}</span>
          </div>
        ) : (
          <div className={`flex-shrink-0 border-b px-4 py-2 text-xs ${TONE_BANNER[tone]}`}>
            <span className="font-medium text-[var(--color-text-primary)]">{bannerText(state)}</span>
          </div>
        ))}

      {programovanieSplit ? (
        // Coding log LEFT + the task plan RIGHT (split view).
        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col">
            <PhaseArtifact phase="programovanie" messages={recent_messages} placeholder={PHASE_PLACEHOLDER.programovanie} />
            {isLivePhase && state?.status === "agent_working" && (
              <div className="flex-shrink-0">
                <PipelineActivityFeed activity={activity} />
              </div>
            )}
          </div>
          {taskPlanSlot && (
            <div className="flex w-80 flex-shrink-0 flex-col border-l border-[var(--color-border-default)]">
              {taskPlanSlot}
            </div>
          )}
        </div>
      ) : (
        <div ref={feedRef} className="min-h-0 flex-1 overflow-y-auto">
          <PhaseArtifact phase={viewedPhase} messages={recent_messages} placeholder={PHASE_PLACEHOLDER[viewedPhase]} />
          {isLivePhase && state?.status === "agent_working" && (
            <div className="flex-shrink-0">
              <PipelineActivityFeed activity={activity} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default ExchangePanel;
