/**
 * TaskPlanPanel — displays and generates the VERSION → EPIC → FEAT → TASK hierarchy.
 *
 * Features:
 *   - "Generate Task Plan" button triggers SSE streaming from the backend.
 *   - Live progress bar during generation.
 *   - Collapsible EPIC → FEAT → TASK tree after generation.
 *   - task_type colour badges per task.
 *
 * Props:
 *   - versionId: UUID of the version whose plan to display / generate.
 *   - projectId: parent project UUID (used for routing context).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Play, RefreshCw, Zap } from "lucide-react";

import { generateTaskPlan } from "../../services/api/taskPlan";
import type { TaskPlanEpic, TaskPlanFeat } from "../../types/taskPlan";

/* ------------------------------------------------------------------ */
/*  Types                                                               */
/* ------------------------------------------------------------------ */

interface Props {
  versionId: string;
  /** When true (ri role), shows the Generate button. */
  canGenerate: boolean;
}

type PanelState =
  | { phase: "idle" }
  | { phase: "generating"; message: string; percent: number }
  | { phase: "done"; epics: TaskPlanEpic[]; epicCount: number; featCount: number; taskCount: number }
  | { phase: "error"; message: string }
  | { phase: "validation_error"; message: string };

/* ------------------------------------------------------------------ */
/*  Helpers                                                             */
/* ------------------------------------------------------------------ */

const TASK_TYPE_BADGE: Record<string, string> = {
  backend: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  frontend: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
  migration: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
  test: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  docs: "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300",
};

function taskTypeBadge(taskType: string) {
  return TASK_TYPE_BADGE[taskType] ?? TASK_TYPE_BADGE.docs;
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                      */
/* ------------------------------------------------------------------ */

function FeatRow({ feat }: { feat: TaskPlanFeat }) {
  const [expanded, setExpanded] = useState(false);
  const taskCount = feat.tasks.length;

  return (
    <div className="border-l-2 border-gray-200 dark:border-gray-600 ml-4 pl-3">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 py-1.5 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-700/50 rounded px-1 transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 flex-shrink-0 text-gray-400" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 flex-shrink-0 text-gray-400" />
        )}
        <span className="font-medium text-gray-800 dark:text-gray-200">
          Feat {feat.number}
        </span>
        <span className="flex-1 text-gray-600 dark:text-gray-400 truncate">
          — {feat.title}
        </span>
        <span className="flex-shrink-0 text-xs text-gray-400 dark:text-gray-500">
          {taskCount} task{taskCount !== 1 ? "s" : ""}
        </span>
      </button>

      {expanded && (
        <div className="mt-1 mb-2 space-y-1 pl-4">
          {feat.tasks.map((task) => (
            <div
              key={`${feat.id}-t${task.number}`}
              className="flex items-start gap-2 rounded px-2 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-700/30"
            >
              <span
                className={`mt-0.5 flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${taskTypeBadge(task.task_type)}`}
              >
                {task.task_type}
              </span>
              <span className="text-gray-700 dark:text-gray-300">{task.title}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EpicRow({ epic }: { epic: TaskPlanEpic }) {
  const [expanded, setExpanded] = useState(true);
  const featCount = epic.feats.length;
  const taskCount = epic.feats.reduce((s, f) => s + f.tasks.length, 0);

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 bg-gray-50 dark:bg-gray-800 px-4 py-2.5 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60 transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 flex-shrink-0 text-gray-500" />
        ) : (
          <ChevronRight className="h-4 w-4 flex-shrink-0 text-gray-500" />
        )}
        <span className="text-xs font-bold text-primary-600 dark:text-primary-400 flex-shrink-0">
          EPIC-{epic.number}
        </span>
        <span className="flex-1 font-semibold text-sm text-gray-900 dark:text-gray-100 truncate">
          {epic.title}
        </span>
        <span className="flex-shrink-0 text-xs text-gray-500 dark:text-gray-400">
          {featCount} feat{featCount !== 1 ? "s" : ""} · {taskCount} task{taskCount !== 1 ? "s" : ""}
        </span>
      </button>

      {expanded && (
        <div className="p-3 space-y-1">
          {epic.feats.map((feat) => (
            <FeatRow key={feat.id} feat={feat} />
          ))}
          {epic.feats.length === 0 && (
            <p className="text-xs text-gray-400 pl-4">No feats</p>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                      */
/* ------------------------------------------------------------------ */

export function TaskPlanPanel({ versionId, canGenerate }: Props) {
  const [state, setState] = useState<PanelState>({ phase: "idle" });
  const abortRef = useRef<AbortController | null>(null);

  // Cancel any in-progress generation on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const startGeneration = useCallback(
    (replaceExisting: boolean) => {
      abortRef.current?.abort();
      setState({ phase: "generating", message: "Spúšťam generovanie…", percent: 0 });

      const controller = generateTaskPlan(
        versionId,
        replaceExisting,
        (message, percent) => {
          setState({ phase: "generating", message, percent });
        },
        (doneEvent) => {
          setState({
            phase: "done",
            epics: doneEvent.plan,
            epicCount: doneEvent.epic_count,
            featCount: doneEvent.feat_count,
            taskCount: doneEvent.task_count,
          });
        },
        (error) => {
          setState({ phase: "error", message: error.message });
        },
        (reason) => {
          setState({ phase: "validation_error", message: reason });
        },
      );
      abortRef.current = controller;
    },
    [versionId],
  );

  /* ---- Idle state ---- */
  if (state.phase === "idle") {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <Zap className="mb-3 h-10 w-10 text-gray-300 dark:text-gray-600" />
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
          Task Plan pre túto verziu ešte nebol vygenerovaný.
        </p>
        {canGenerate && (
          <button
            type="button"
            onClick={() => startGeneration(false)}
            className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 transition-colors"
          >
            <Play className="h-4 w-4" />
            Generovať Task Plan
          </button>
        )}
      </div>
    );
  }

  /* ---- Generating state ---- */
  if (state.phase === "generating") {
    return (
      <div className="py-8 px-4">
        <div className="flex items-center gap-3 mb-4">
          <Loader2 className="h-5 w-5 animate-spin text-primary-500" />
          <p className="text-sm font-medium text-gray-700 dark:text-gray-300">
            {state.message}
          </p>
        </div>
        <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
          <div
            className="bg-primary-500 h-2 rounded-full transition-all duration-500"
            style={{ width: `${state.percent}%` }}
          />
        </div>
        <p className="mt-1 text-xs text-gray-400 dark:text-gray-500 text-right">
          {state.percent}%
        </p>
        <button
          type="button"
          onClick={() => {
            abortRef.current?.abort();
            setState({ phase: "idle" });
          }}
          className="mt-4 text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 underline"
        >
          Zrušiť
        </button>
      </div>
    );
  }

  /* ---- Error states ---- */
  if (state.phase === "error" || state.phase === "validation_error") {
    const isValidation = state.phase === "validation_error";
    return (
      <div className="py-6 px-4">
        <div
          role="alert"
          className={`rounded-lg border px-4 py-3 text-sm mb-4 ${
            isValidation
              ? "border-yellow-300 bg-yellow-50 text-yellow-800 dark:border-yellow-700 dark:bg-yellow-900/20 dark:text-yellow-300"
              : "border-red-300 bg-red-50 text-red-800 dark:border-red-700 dark:bg-red-900/20 dark:text-red-300"
          }`}
        >
          <strong className="block mb-1">
            {isValidation ? "Validácia zlyhala" : "Chyba generovania"}
          </strong>
          {state.message}
        </div>
        {canGenerate && (
          <button
            type="button"
            onClick={() => startGeneration(false)}
            className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 transition-colors"
          >
            <RefreshCw className="h-4 w-4" />
            Skúsiť znovu
          </button>
        )}
      </div>
    );
  }

  /* ---- Done state — show the plan ---- */
  const { epics, epicCount, featCount, taskCount } = state;

  return (
    <div className="space-y-4">
      {/* Summary bar */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-4 text-sm text-gray-600 dark:text-gray-400">
          <span>
            <strong className="text-gray-900 dark:text-gray-100">{epicCount}</strong> EPICs
          </span>
          <span>
            <strong className="text-gray-900 dark:text-gray-100">{featCount}</strong> Feats
          </span>
          <span>
            <strong className="text-gray-900 dark:text-gray-100">{taskCount}</strong> Tasks
          </span>
        </div>
        {canGenerate && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => startGeneration(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
              title="Vymazať existujúci plán a vygenerovať nový"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Regenerovať
            </button>
          </div>
        )}
      </div>

      {/* Epic tree */}
      <div className="space-y-3">
        {epics.map((epic) => (
          <EpicRow key={epic.id} epic={epic} />
        ))}
        {epics.length === 0 && (
          <p className="text-sm text-gray-400 dark:text-gray-500 text-center py-8">
            Task Plan je prázdny.
          </p>
        )}
      </div>
    </div>
  );
}
