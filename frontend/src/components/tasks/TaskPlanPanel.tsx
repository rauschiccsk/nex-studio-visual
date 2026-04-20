/**
 * TaskPlanPanel — interactive VERSION → EPIC → FEAT → TASK hierarchy.
 *
 * Features (mirrors NEX Command TaskPlanPanel):
 *   - Load existing plan from DB on mount.
 *   - Generate / Regenerate via SSE streaming.
 *   - Append Epic (non-destructive SSE).
 *   - Click task status to cycle: todo → in_progress → done → failed → todo.
 *   - Priority badges (normal/high/urgent) on tasks.
 *   - Derived Feat status (auto-computed from tasks) shown as badge.
 *   - Derived Epic status shown as badge.
 *   - Add task inline form per Feat.
 *   - Add feat inline form per Epic.
 *   - Delete task / feat / epic with confirmation dialogs.
 *   - Reset Tasks / Reset Plan with confirmation dialogs.
 *   - Collapse/expand state persisted in localStorage per Epic/Feat.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  X,
  Zap,
} from "lucide-react";

import {
  appendEpic,
  createFeat,
  createTask,
  deleteEpic,
  deleteFeat,
  deleteTask,
  executeFeat,
  fetchTaskPlan,
  generateTaskPlan,
  patchTask,
  resetPlan,
  resetTasks,
} from "../../services/api/taskPlan";
import type { FeatExecuteEvent } from "../../services/api/taskPlan";
import type {
  EpicStatus,
  FeatStatus,
  TaskPlanEpic,
  TaskPlanFeat,
  TaskPlanTask,
  TaskPriority,
  TaskStatus,
} from "../../types/taskPlan";

/* ------------------------------------------------------------------ */
/*  Props                                                               */
/* ------------------------------------------------------------------ */

interface Props {
  versionId: string;
  canGenerate: boolean;
}

/* ------------------------------------------------------------------ */
/*  Panel state machine                                                 */
/* ------------------------------------------------------------------ */

type PanelState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "generating"; message: string; percent: number; subtype: "generate" | "append" }
  | { phase: "done"; epics: TaskPlanEpic[]; epicCount: number; featCount: number; taskCount: number }
  | { phase: "error"; message: string }
  | { phase: "validation_error"; message: string };

/* ------------------------------------------------------------------ */
/*  Status/priority helpers                                             */
/* ------------------------------------------------------------------ */

const TASK_STATUS_CYCLE: TaskStatus[] = ["todo", "in_progress", "done", "failed"];

function nextTaskStatus(current: TaskStatus): TaskStatus {
  const idx = TASK_STATUS_CYCLE.indexOf(current);
  return TASK_STATUS_CYCLE[(idx + 1) % TASK_STATUS_CYCLE.length] ?? "todo";
}

const TASK_TYPE_BADGE: Record<string, string> = {
  backend: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  frontend: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
  migration: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
  test: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  docs: "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300",
};

const TASK_STATUS_DOT: Record<TaskStatus, string> = {
  todo: "bg-gray-300 dark:bg-gray-600",
  in_progress: "bg-yellow-400 dark:bg-yellow-500",
  done: "bg-green-500 dark:bg-green-400",
  failed: "bg-red-500 dark:bg-red-400",
};

const FEAT_STATUS_BADGE: Record<FeatStatus, string> = {
  todo: "bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400",
  in_progress: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-400",
  done: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400",
  failed: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400",
};

const EPIC_STATUS_BADGE: Record<EpicStatus, string> = {
  planned: "bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400",
  in_progress: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-400",
  done: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400",
};

const PRIORITY_BADGE: Record<TaskPriority, string> = {
  normal: "",
  high: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  urgent: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
};

/* ------------------------------------------------------------------ */
/*  Derived status helpers (mirrors NEX Command rebuildFeats/Epics)    */
/* ------------------------------------------------------------------ */

function deriveFeatStatus(tasks: TaskPlanTask[]): FeatStatus {
  if (tasks.length === 0) return "todo";
  const statuses = new Set(tasks.map((t) => t.status));
  if (statuses.size === 1 && statuses.has("done")) return "done";
  if (statuses.has("in_progress")) return "in_progress";
  if (statuses.has("failed")) return "failed";
  return "todo";
}

function deriveEpicStatus(feats: TaskPlanFeat[]): EpicStatus {
  if (feats.length === 0) return "planned";
  const statuses = new Set(feats.map((f) => f.status));
  if (statuses.size === 1 && statuses.has("done")) return "done";
  if (statuses.has("in_progress") || statuses.has("failed")) return "in_progress";
  return "planned";
}

/* ------------------------------------------------------------------ */
/*  localStorage collapse helpers                                       */
/* ------------------------------------------------------------------ */

function lsGet(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === "true";
  } catch {
    return fallback;
  }
}

function lsSet(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    /* ignore */
  }
}

/* ------------------------------------------------------------------ */
/*  Confirm dialog                                                      */
/* ------------------------------------------------------------------ */

interface ConfirmDialogProps {
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
  danger?: boolean;
}

function ConfirmDialog({ message, onConfirm, onCancel, danger = false }: ConfirmDialogProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 dark:bg-black/60">
      <div className="mx-4 w-full max-w-sm rounded-lg bg-white shadow-xl dark:bg-gray-800 p-5">
        <p className="text-sm text-gray-700 dark:text-gray-300 mb-4">{message}</p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            Zrušiť
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className={`rounded px-3 py-1.5 text-sm font-medium text-white ${
              danger
                ? "bg-red-600 hover:bg-red-700"
                : "bg-primary-600 hover:bg-primary-700"
            }`}
          >
            Potvrdiť
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Task row                                                            */
/* ------------------------------------------------------------------ */

interface TaskRowProps {
  task: TaskPlanTask;
  canGenerate: boolean;
  onStatusChange: (taskId: string, newStatus: TaskStatus) => void;
  onDelete: (taskId: string) => void;
}

function TaskRow({ task, canGenerate, onStatusChange, onDelete }: TaskRowProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);

  return (
    <div className="flex items-start gap-2 rounded px-2 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-700/30 group">
      {/* Status dot — clickable */}
      <button
        type="button"
        title={`Status: ${task.status} — klikni pre zmenu`}
        onClick={() => canGenerate && onStatusChange(task.id, nextTaskStatus(task.status))}
        className={`mt-0.5 h-3 w-3 flex-shrink-0 rounded-full ${TASK_STATUS_DOT[task.status]} ${canGenerate ? "cursor-pointer hover:opacity-70" : "cursor-default"}`}
        disabled={!canGenerate}
      />

      {/* task_type badge */}
      <span
        className={`mt-0.5 flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${TASK_TYPE_BADGE[task.task_type] ?? TASK_TYPE_BADGE.docs}`}
      >
        {task.task_type}
      </span>

      {/* title */}
      <span className="flex-1 text-gray-700 dark:text-gray-300">{task.title}</span>

      {/* priority badge */}
      {task.priority !== "normal" && (
        <span className={`flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${PRIORITY_BADGE[task.priority]}`}>
          {task.priority}
        </span>
      )}

      {/* delete */}
      {canGenerate && (
        <button
          type="button"
          title="Zmazať task"
          onClick={() => setConfirmDelete(true)}
          className="flex-shrink-0 opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 transition-opacity"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      )}

      {confirmDelete && (
        <ConfirmDialog
          message={`Zmazať task "${task.title}"?`}
          danger
          onConfirm={() => {
            setConfirmDelete(false);
            onDelete(task.id);
          }}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Feat row                                                            */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/*  Feat execution state                                                */
/* ------------------------------------------------------------------ */

interface FeatExecState {
  running: boolean;
  output: string;
  activeTaskId: string | null;
  taskStatuses: Record<string, string>; // taskId → "in_progress" | "done" | "failed"
  featStatus: string | null;
  error: string | null;
}

const EXEC_IDLE: FeatExecState = {
  running: false,
  output: "",
  activeTaskId: null,
  taskStatuses: {},
  featStatus: null,
  error: null,
};

interface FeatRowProps {
  feat: TaskPlanFeat;
  epicId: string;
  canGenerate: boolean;
  onTaskStatusChange: (taskId: string, newStatus: TaskStatus) => void;
  onTaskDelete: (featId: string, taskId: string) => void;
  onTaskAdd: (featId: string, title: string, taskType: string) => void;
  onFeatDelete: (featId: string) => void;
  onFeatExecuted: (featId: string) => void;
}

function FeatRow({ feat, canGenerate, onTaskStatusChange, onTaskDelete, onTaskAdd, onFeatDelete, onFeatExecuted }: FeatRowProps) {
  const lsKey = `taskPlan.feat.${feat.id}`;
  const [expanded, setExpanded] = useState(() => lsGet(lsKey, true));
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [addingTask, setAddingTask] = useState(false);
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [newTaskType, setNewTaskType] = useState<"backend" | "frontend" | "migration" | "test" | "docs">("backend");
  const [exec, setExec] = useState<FeatExecState>(EXEC_IDLE);
  const [outputExpanded, setOutputExpanded] = useState(true);
  const execAbortRef = useRef<AbortController | null>(null);
  const outputRef = useRef<HTMLPreElement | null>(null);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    lsSet(lsKey, next);
  };

  const startExecute = () => {
    execAbortRef.current?.abort();
    setExec({ running: true, output: "", activeTaskId: null, taskStatuses: {}, featStatus: null, error: null });
    setOutputExpanded(true);

    const controller = executeFeat(
      feat.id,
      (event: FeatExecuteEvent) => {
        switch (event.type) {
          case "task_start":
            setExec((prev) => ({
              ...prev,
              activeTaskId: event.task_id,
              output: prev.output + `\n▶ Task ${event.task_number}: ${event.task_title}\n`,
            }));
            break;
          case "chunk":
            setExec((prev) => {
              const updated = { ...prev, output: prev.output + event.text };
              // Auto-scroll
              setTimeout(() => {
                if (outputRef.current) {
                  outputRef.current.scrollTop = outputRef.current.scrollHeight;
                }
              }, 0);
              return updated;
            });
            break;
          case "task_done":
            setExec((prev) => ({
              ...prev,
              activeTaskId: null,
              taskStatuses: { ...prev.taskStatuses, [event.task_id]: event.status },
              output: prev.output + `\n${event.status === "done" ? "✓" : "✗"} Task ${event.status.toUpperCase()}\n`,
            }));
            break;
          case "feat_done":
            setExec((prev) => ({
              ...prev,
              running: false,
              featStatus: event.feat_status,
              output: prev.output + `\n=== Feat ${event.feat_status.toUpperCase()} ===\n`,
            }));
            onFeatExecuted(feat.id);
            break;
          case "error":
            setExec((prev) => ({
              ...prev,
              running: false,
              error: event.content,
              output: prev.output + `\n[ERROR] ${event.content}\n`,
            }));
            break;
        }
      },
      (err) => {
        setExec((prev) => ({
          ...prev,
          running: false,
          error: err.message,
          output: prev.output + `\n[ERROR] ${err.message}\n`,
        }));
      },
    );
    execAbortRef.current = controller;
  };

  const stopExecute = () => {
    execAbortRef.current?.abort();
    setExec((prev) => ({ ...prev, running: false, output: prev.output + "\n[CANCELLED]\n" }));
  };

  const submitTask = () => {
    const t = newTaskTitle.trim();
    if (!t) return;
    onTaskAdd(feat.id, t, newTaskType);
    setNewTaskTitle("");
    setAddingTask(false);
  };

  const derivedStatus = deriveFeatStatus(feat.tasks);

  return (
    <div className="border-l-2 border-gray-200 dark:border-gray-600 ml-4 pl-3">
      <div className="flex items-center gap-1 group">
        <button
          type="button"
          onClick={toggle}
          className="flex flex-1 items-center gap-2 py-1.5 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-700/50 rounded px-1 transition-colors"
        >
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 flex-shrink-0 text-gray-400" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 flex-shrink-0 text-gray-400" />
          )}
          <span className="font-medium text-gray-800 dark:text-gray-200">Feat {feat.number}</span>
          <span className="flex-1 text-gray-600 dark:text-gray-400 truncate">— {feat.title}</span>
          <span className={`flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${FEAT_STATUS_BADGE[derivedStatus]}`}>
            {derivedStatus}
          </span>
          <span className="flex-shrink-0 text-xs text-gray-400 dark:text-gray-500">
            {feat.tasks.length} tasks
          </span>
        </button>

        {canGenerate && (
          <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
            {/* Execute feat */}
            {exec.running ? (
              <button
                type="button"
                title="Zastaviť exekúciu"
                onClick={stopExecute}
                className="rounded p-1 text-yellow-500 hover:text-yellow-700 animate-pulse"
              >
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              </button>
            ) : feat.status !== "done" && (
              <button
                type="button"
                title="Spustiť exekúciu featu cez CC"
                onClick={startExecute}
                className="rounded p-1 text-gray-400 hover:text-green-600 dark:hover:text-green-400"
              >
                <Play className="h-3.5 w-3.5" />
              </button>
            )}
            <button
              type="button"
              title="Pridať task"
              onClick={() => setAddingTask(true)}
              className="rounded p-1 text-gray-400 hover:text-primary-600 dark:hover:text-primary-400"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              title="Zmazať feat"
              onClick={() => setConfirmDelete(true)}
              className="rounded p-1 text-gray-400 hover:text-red-500"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>

      {expanded && (
        <div className="mt-1 mb-2 space-y-0.5 pl-2">
          {feat.tasks.map((task) => (
            <TaskRow
              key={task.id}
              task={task}
              canGenerate={canGenerate}
              onStatusChange={onTaskStatusChange}
              onDelete={(taskId) => onTaskDelete(feat.id, taskId)}
            />
          ))}
          {feat.tasks.length === 0 && (
            <p className="text-xs text-gray-400 py-1 pl-2">Žiadne tasky</p>
          )}

          {addingTask && (
            <div className="mt-2 flex items-center gap-1 pl-2">
              <select
                value={newTaskType}
                onChange={(e) => setNewTaskType(e.target.value as typeof newTaskType)}
                className="rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-xs px-1 py-1 text-gray-700 dark:text-gray-300"
              >
                <option value="backend">backend</option>
                <option value="frontend">frontend</option>
                <option value="migration">migration</option>
                <option value="test">test</option>
                <option value="docs">docs</option>
              </select>
              <input
                autoFocus
                type="text"
                value={newTaskTitle}
                onChange={(e) => setNewTaskTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submitTask();
                  if (e.key === "Escape") setAddingTask(false);
                }}
                placeholder="Názov tasku…"
                className="flex-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-xs px-2 py-1 text-gray-900 dark:text-gray-100"
              />
              <button
                type="button"
                onClick={submitTask}
                className="rounded bg-primary-600 px-2 py-1 text-xs text-white hover:bg-primary-700"
              >
                Pridať
              </button>
              <button
                type="button"
                onClick={() => setAddingTask(false)}
                className="rounded p-1 text-gray-400 hover:text-gray-600"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {/* CC execution output panel */}
          {(exec.running || exec.output) && (
            <div className="mt-2 ml-2 rounded border border-gray-700 dark:border-gray-600 bg-gray-900 overflow-hidden">
              <div className="flex items-center justify-between px-2 py-1 border-b border-gray-700">
                <span className="text-[10px] font-mono text-gray-400">
                  {exec.running ? (
                    <span className="flex items-center gap-1">
                      <Loader2 className="h-3 w-3 animate-spin text-yellow-400" />
                      <span className="text-yellow-400">Executing…</span>
                    </span>
                  ) : exec.featStatus === "done" ? (
                    <span className="text-green-400">✓ Done</span>
                  ) : (
                    <span className="text-red-400">✗ Failed</span>
                  )}
                </span>
                <div className="flex gap-1">
                  <button
                    type="button"
                    onClick={() => setOutputExpanded((v) => !v)}
                    className="text-gray-500 hover:text-gray-300 text-[10px] font-mono"
                  >
                    {outputExpanded ? "▲ collapse" : "▼ expand"}
                  </button>
                  {!exec.running && (
                    <button
                      type="button"
                      onClick={() => setExec(EXEC_IDLE)}
                      className="ml-1 text-gray-500 hover:text-gray-300"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  )}
                </div>
              </div>
              {outputExpanded && (
                <pre
                  ref={outputRef}
                  className="text-[10px] font-mono text-gray-200 p-2 max-h-64 overflow-y-auto whitespace-pre-wrap leading-relaxed"
                >
                  {exec.output || " "}
                </pre>
              )}
            </div>
          )}
        </div>
      )}

      {confirmDelete && (
        <ConfirmDialog
          message={`Zmazať feat "${feat.title}" vrátane všetkých taskov?`}
          danger
          onConfirm={() => {
            setConfirmDelete(false);
            onFeatDelete(feat.id);
          }}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Epic row                                                            */
/* ------------------------------------------------------------------ */

interface EpicRowProps {
  epic: TaskPlanEpic;
  canGenerate: boolean;
  onTaskStatusChange: (epicId: string, featId: string, taskId: string, newStatus: TaskStatus) => void;
  onTaskDelete: (epicId: string, featId: string, taskId: string) => void;
  onTaskAdd: (epicId: string, featId: string, title: string, taskType: string) => void;
  onFeatDelete: (epicId: string, featId: string) => void;
  onFeatAdd: (epicId: string, title: string) => void;
  onEpicDelete: (epicId: string) => void;
  onFeatExecuted: (featId: string) => void;
}

function EpicRow({
  epic,
  canGenerate,
  onTaskStatusChange,
  onTaskDelete,
  onTaskAdd,
  onFeatDelete,
  onFeatAdd,
  onEpicDelete,
  onFeatExecuted,
}: EpicRowProps) {
  const lsKey = `taskPlan.epic.${epic.id}`;
  const [expanded, setExpanded] = useState(() => lsGet(lsKey, true));
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [addingFeat, setAddingFeat] = useState(false);
  const [newFeatTitle, setNewFeatTitle] = useState("");

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    lsSet(lsKey, next);
  };

  const submitFeat = () => {
    const t = newFeatTitle.trim();
    if (!t) return;
    onFeatAdd(epic.id, t);
    setNewFeatTitle("");
    setAddingFeat(false);
  };

  const derivedStatus = deriveEpicStatus(epic.feats);
  const featCount = epic.feats.length;
  const taskCount = epic.feats.reduce((s, f) => s + f.tasks.length, 0);

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
      <div className="flex items-center bg-gray-50 dark:bg-gray-800 group">
        <button
          type="button"
          onClick={toggle}
          className="flex flex-1 items-center gap-2 px-4 py-2.5 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60 transition-colors"
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
          <span className={`flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${EPIC_STATUS_BADGE[derivedStatus]}`}>
            {derivedStatus}
          </span>
          <span className="flex-shrink-0 text-xs text-gray-500 dark:text-gray-400">
            {featCount} feats · {taskCount} tasks
          </span>
        </button>

        {canGenerate && (
          <div className="flex gap-1 pr-3 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
            <button
              type="button"
              title="Pridať feat"
              onClick={() => setAddingFeat(true)}
              className="rounded p-1 text-gray-400 hover:text-primary-600 dark:hover:text-primary-400"
            >
              <Plus className="h-4 w-4" />
            </button>
            <button
              type="button"
              title="Zmazať epic"
              onClick={() => setConfirmDelete(true)}
              className="rounded p-1 text-gray-400 hover:text-red-500"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>

      {expanded && (
        <div className="p-3 space-y-1">
          {epic.feats.map((feat) => (
            <FeatRow
              key={feat.id}
              feat={feat}
              epicId={epic.id}
              canGenerate={canGenerate}
              onTaskStatusChange={(taskId, newStatus) =>
                onTaskStatusChange(epic.id, feat.id, taskId, newStatus)
              }
              onTaskDelete={(featId, taskId) => onTaskDelete(epic.id, featId, taskId)}
              onTaskAdd={(featId, title, taskType) => onTaskAdd(epic.id, featId, title, taskType)}
              onFeatDelete={(featId) => onFeatDelete(epic.id, featId)}
              onFeatExecuted={onFeatExecuted}
            />
          ))}
          {epic.feats.length === 0 && (
            <p className="text-xs text-gray-400 pl-4">Žiadne featy</p>
          )}

          {addingFeat && (
            <div className="mt-2 ml-4 flex items-center gap-2">
              <input
                autoFocus
                type="text"
                value={newFeatTitle}
                onChange={(e) => setNewFeatTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submitFeat();
                  if (e.key === "Escape") setAddingFeat(false);
                }}
                placeholder="Názov featu…"
                className="flex-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-sm px-2 py-1 text-gray-900 dark:text-gray-100"
              />
              <button
                type="button"
                onClick={submitFeat}
                className="rounded bg-primary-600 px-2 py-1 text-xs text-white hover:bg-primary-700"
              >
                Pridať
              </button>
              <button
                type="button"
                onClick={() => setAddingFeat(false)}
                className="rounded p-1 text-gray-400 hover:text-gray-600"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          )}
        </div>
      )}

      {confirmDelete && (
        <ConfirmDialog
          message={`Zmazať EPIC-${epic.number} "${epic.title}" vrátane všetkých featov a taskov?`}
          danger
          onConfirm={() => {
            setConfirmDelete(false);
            onEpicDelete(epic.id);
          }}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                      */
/* ------------------------------------------------------------------ */

export function TaskPlanPanel({ versionId, canGenerate }: Props) {
  const [state, setState] = useState<PanelState>({ phase: "loading" });
  const abortRef = useRef<AbortController | null>(null);
  const [confirmResetTasks, setConfirmResetTasks] = useState(false);
  const [confirmResetPlan, setConfirmResetPlan] = useState(false);

  /* ---- Load existing plan from DB on mount ---- */
  useEffect(() => {
    let cancelled = false;
    fetchTaskPlan(versionId).then((data) => {
      if (cancelled) return;
      if (data && data.plan.length > 0) {
        setState({
          phase: "done",
          epics: data.plan,
          epicCount: data.epic_count,
          featCount: data.feat_count,
          taskCount: data.task_count,
        });
      } else {
        setState({ phase: "idle" });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [versionId]);

  /* ---- Cancel generation on unmount ---- */
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  /* ---- Generation ---- */
  const startGeneration = useCallback(
    (replaceExisting: boolean) => {
      abortRef.current?.abort();
      setState({ phase: "generating", message: "Spúšťam generovanie…", percent: 0, subtype: "generate" });

      const controller = generateTaskPlan(
        versionId,
        replaceExisting,
        (message, percent) => {
          setState({ phase: "generating", message, percent, subtype: "generate" });
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
        (error) => setState({ phase: "error", message: error.message }),
        (reason) => setState({ phase: "validation_error", message: reason }),
      );
      abortRef.current = controller;
    },
    [versionId],
  );

  /* ---- Append Epic ---- */
  const startAppendEpic = useCallback(() => {
    if (state.phase !== "done") return;
    const prevEpics = state.epics;
    abortRef.current?.abort();
    setState({ phase: "generating", message: "Pridávam nový EPIC…", percent: 0, subtype: "append" });

    const controller = appendEpic(
      versionId,
      (message, percent) => {
        setState({ phase: "generating", message, percent, subtype: "append" });
      },
      (doneEvent) => {
        setState({
          phase: "done",
          epics: [...prevEpics, ...doneEvent.plan],
          epicCount: prevEpics.length + doneEvent.epic_count,
          featCount: prevEpics.reduce((s, e) => s + e.feats.length, 0) + doneEvent.feat_count,
          taskCount:
            prevEpics.reduce((s, e) => s + e.feats.reduce((fs, f) => fs + f.tasks.length, 0), 0) +
            doneEvent.task_count,
        });
      },
      (error) => {
        setState({
          phase: "done",
          epics: prevEpics,
          epicCount: prevEpics.length,
          featCount: prevEpics.reduce((s, e) => s + e.feats.length, 0),
          taskCount: prevEpics.reduce((s, e) => s + e.feats.reduce((fs, f) => fs + f.tasks.length, 0), 0),
        });
        alert(`Chyba: ${error.message}`);
      },
      (reason) => {
        setState({
          phase: "done",
          epics: prevEpics,
          epicCount: prevEpics.length,
          featCount: prevEpics.reduce((s, e) => s + e.feats.length, 0),
          taskCount: prevEpics.reduce((s, e) => s + e.feats.reduce((fs, f) => fs + f.tasks.length, 0), 0),
        });
        alert(`Validácia: ${reason}`);
      },
    );
    abortRef.current = controller;
  }, [state, versionId]);

  /* ---- Mutators (local state + API) ---- */

  const handleTaskStatusChange = useCallback(
    async (epicId: string, featId: string, taskId: string, newStatus: TaskStatus) => {
      if (state.phase !== "done") return;
      // Optimistic update
      setState((prev) => {
        if (prev.phase !== "done") return prev;
        return {
          ...prev,
          epics: prev.epics.map((e) =>
            e.id !== epicId
              ? e
              : {
                  ...e,
                  feats: e.feats.map((f) =>
                    f.id !== featId
                      ? f
                      : {
                          ...f,
                          tasks: f.tasks.map((t) =>
                            t.id !== taskId ? t : { ...t, status: newStatus },
                          ),
                        },
                  ),
                },
          ),
        };
      });
      try {
        await patchTask(taskId, { status: newStatus });
      } catch {
        // Revert on failure — re-fetch from DB
        const data = await fetchTaskPlan(versionId);
        if (data) {
          setState({
            phase: "done",
            epics: data.plan,
            epicCount: data.epic_count,
            featCount: data.feat_count,
            taskCount: data.task_count,
          });
        }
      }
    },
    [state, versionId],
  );

  const handleTaskDelete = useCallback(
    async (epicId: string, featId: string, taskId: string) => {
      try {
        await deleteTask(taskId);
        setState((prev) => {
          if (prev.phase !== "done") return prev;
          return {
            ...prev,
            epics: prev.epics.map((e) =>
              e.id !== epicId
                ? e
                : {
                    ...e,
                    feats: e.feats.map((f) =>
                      f.id !== featId
                        ? f
                        : { ...f, tasks: f.tasks.filter((t) => t.id !== taskId) },
                    ),
                  },
            ),
          };
        });
      } catch (err) {
        alert(`Zmazanie tasku zlyhalo: ${String(err)}`);
      }
    },
    [],
  );

  const handleTaskAdd = useCallback(
    async (epicId: string, featId: string, title: string, taskType: string) => {
      try {
        const created = await createTask({
          feat_id: featId,
          title,
          task_type: taskType as "backend" | "frontend" | "migration" | "test" | "docs",
        });
        const newTask: TaskPlanTask = {
          id: created.id,
          number: created.number,
          title,
          description: "",
          task_type: taskType as TaskPlanTask["task_type"],
          checklist_type: null,
          status: "todo",
          priority: "normal",
        };
        setState((prev) => {
          if (prev.phase !== "done") return prev;
          return {
            ...prev,
            epics: prev.epics.map((e) =>
              e.id !== epicId
                ? e
                : {
                    ...e,
                    feats: e.feats.map((f) =>
                      f.id !== featId ? f : { ...f, tasks: [...f.tasks, newTask] },
                    ),
                  },
            ),
          };
        });
      } catch (err) {
        alert(`Vytvorenie tasku zlyhalo: ${String(err)}`);
      }
    },
    [],
  );

  const handleFeatDelete = useCallback(async (epicId: string, featId: string) => {
    try {
      await deleteFeat(featId);
      setState((prev) => {
        if (prev.phase !== "done") return prev;
        return {
          ...prev,
          epics: prev.epics.map((e) =>
            e.id !== epicId ? e : { ...e, feats: e.feats.filter((f) => f.id !== featId) },
          ),
        };
      });
    } catch (err) {
      alert(`Zmazanie featu zlyhalo: ${String(err)}`);
    }
  }, []);

  const handleFeatAdd = useCallback(async (epicId: string, title: string) => {
    try {
      const created = await createFeat(epicId, title);
      const newFeat: TaskPlanFeat = {
        id: created.id,
        number: created.number,
        title,
        status: "todo",
        tasks: [],
      };
      setState((prev) => {
        if (prev.phase !== "done") return prev;
        return {
          ...prev,
          epics: prev.epics.map((e) =>
            e.id !== epicId ? e : { ...e, feats: [...e.feats, newFeat] },
          ),
        };
      });
    } catch (err) {
      alert(`Vytvorenie featu zlyhalo: ${String(err)}`);
    }
  }, []);

  const handleEpicDelete = useCallback(async (epicId: string) => {
    try {
      await deleteEpic(epicId);
      setState((prev) => {
        if (prev.phase !== "done") return prev;
        const epics = prev.epics.filter((e) => e.id !== epicId);
        return {
          ...prev,
          epics,
          epicCount: epics.length,
          featCount: epics.reduce((s, e) => s + e.feats.length, 0),
          taskCount: epics.reduce((s, e) => s + e.feats.reduce((fs, f) => fs + f.tasks.length, 0), 0),
        };
      });
    } catch (err) {
      alert(`Zmazanie epicu zlyhalo: ${String(err)}`);
    }
  }, []);

  const handleResetTasks = useCallback(async () => {
    try {
      await resetTasks(versionId);
      const data = await fetchTaskPlan(versionId);
      if (data) {
        setState({
          phase: "done",
          epics: data.plan,
          epicCount: data.epic_count,
          featCount: data.feat_count,
          taskCount: data.task_count,
        });
      }
    } catch (err) {
      alert(`Reset taskov zlyhal: ${String(err)}`);
    }
  }, [versionId]);

  const handleResetPlan = useCallback(async () => {
    try {
      await resetPlan(versionId);
      setState({ phase: "idle" });
    } catch (err) {
      alert(`Reset plánu zlyhal: ${String(err)}`);
    }
  }, [versionId]);

  // Re-fetch plan after feat execution to sync task statuses from DB.
  const handleFeatExecuted = useCallback(async () => {
    const data = await fetchTaskPlan(versionId);
    if (data) {
      setState({
        phase: "done",
        epics: data.plan,
        epicCount: data.epic_count,
        featCount: data.feat_count,
        taskCount: data.task_count,
      });
    }
  }, [versionId]);

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */

  /* Loading */
  if (state.phase === "loading") {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-5 w-5 animate-spin text-gray-400" />
      </div>
    );
  }

  /* Idle */
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

  /* Generating */
  if (state.phase === "generating") {
    return (
      <div className="py-8 px-4">
        <div className="flex items-center gap-3 mb-4">
          <Loader2 className="h-5 w-5 animate-spin text-primary-500" />
          <p className="text-sm font-medium text-gray-700 dark:text-gray-300">{state.message}</p>
        </div>
        <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
          <div
            className="bg-primary-500 h-2 rounded-full transition-all duration-500"
            style={{ width: `${state.percent}%` }}
          />
        </div>
        <p className="mt-1 text-xs text-gray-400 dark:text-gray-500 text-right">{state.percent}%</p>
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

  /* Error states */
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

  /* Done — full interactive plan */
  const { epics, epicCount, featCount, taskCount } = state;

  return (
    <div className="space-y-4">
      {/* Summary + action bar */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-4 text-sm text-gray-600 dark:text-gray-400">
          <span><strong className="text-gray-900 dark:text-gray-100">{epicCount}</strong> EPICs</span>
          <span><strong className="text-gray-900 dark:text-gray-100">{featCount}</strong> Feats</span>
          <span><strong className="text-gray-900 dark:text-gray-100">{taskCount}</strong> Tasks</span>
        </div>

        {canGenerate && (
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              onClick={startAppendEpic}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
              title="Pridaj nový EPIC bez zmazania existujúcich"
            >
              <Plus className="h-3.5 w-3.5" />
              Append EPIC
            </button>
            <button
              type="button"
              onClick={() => setConfirmResetTasks(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
            >
              Reset Tasks
            </button>
            <button
              type="button"
              onClick={() => startGeneration(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
              title="Vymazať existujúci plán a vygenerovať nový"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Regenerovať
            </button>
            <button
              type="button"
              onClick={() => setConfirmResetPlan(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-red-300 dark:border-red-700 px-3 py-1.5 text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
              title="Zmazať celý plán"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Reset Plan
            </button>
          </div>
        )}
      </div>

      {/* Epic tree */}
      <div className="space-y-3">
        {epics.map((epic) => (
          <EpicRow
            key={epic.id}
            epic={epic}
            canGenerate={canGenerate}
            onTaskStatusChange={handleTaskStatusChange}
            onTaskDelete={handleTaskDelete}
            onTaskAdd={handleTaskAdd}
            onFeatDelete={handleFeatDelete}
            onFeatAdd={handleFeatAdd}
            onEpicDelete={handleEpicDelete}
            onFeatExecuted={handleFeatExecuted}
          />
        ))}
        {epics.length === 0 && (
          <p className="text-sm text-gray-400 dark:text-gray-500 text-center py-8">
            Task Plan je prázdny.
          </p>
        )}
      </div>

      {/* Confirm dialogs */}
      {confirmResetTasks && (
        <ConfirmDialog
          message="Nastaviť všetky tasky späť na 'todo'? Záznamy zostanú, iba statusy sa vynulujú."
          onConfirm={() => {
            setConfirmResetTasks(false);
            void handleResetTasks();
          }}
          onCancel={() => setConfirmResetTasks(false)}
        />
      )}
      {confirmResetPlan && (
        <ConfirmDialog
          message="Zmazať celý Task Plan? Táto akcia je nevratná — všetky EPICy, Featy a Tasky budú zmazané."
          danger
          onConfirm={() => {
            setConfirmResetPlan(false);
            void handleResetPlan();
          }}
          onCancel={() => setConfirmResetPlan(false)}
        />
      )}
    </div>
  );
}
