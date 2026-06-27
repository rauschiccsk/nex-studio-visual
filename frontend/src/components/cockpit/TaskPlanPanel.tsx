// Task-plan tree (NEX Studio v2, CR-V2-023; design §4.5). The task plan (EPIC → FEAT → TASK) is the
// last part of the Návrh design document and also drives the Programovanie split view (plan on the
// RIGHT). The Manažér sees the decomposition with live per-node status and which task is underway.
//
// v2 changes vs v1 (CR-NS-020 CR-5):
//   - The per-task TaskAuditPanel / readTaskAudit are REMOVED — v2 has NO per-task Auditor (design §2.2);
//     the Auditor verdict is a single end-of-build artifact in the Verifikácia tab, not per task.
//   - Expand/collapse state PERSISTS across navigation + reload via localStorage, per browser, per
//     version (OQ-8: per-browser localStorage is sufficient for a UI convenience — zero backend).
//   - Level colour-coding: EPIC = purple, FEAT = yellow, TASK = blue (design §4.5). These are the
//     node-LEVEL colours (on the title text); the status DOT keeps the unified status palette (CR-NS-028).

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";

import { getTaskPlan } from "../../services/api/versions";
import type {
  TaskPlanResponse,
  TaskPlanTaskNode,
  TaskPlanEpicNode,
  TaskPlanFeatNode,
} from "../../types/task-plan";
import type { PipelineMessage } from "../../services/api/pipeline";
import { TASK_STATUS_LABELS, TASK_STATUS_TONE, TONE_DOT } from "./labels";

interface Props {
  versionId: string;
  /** Live message stream (board.recent_messages) — the debounce key for tree-freshness refetch. */
  messages: PipelineMessage[];
}

// Level colour-coding (design §4.5): EPIC = purple, FEAT = yellow, TASK = blue. Applied to the node
// TITLE text (the status dot stays on the unified status palette — CR-NS-028). Each colour is
// light-readable + dark-readable via `text-X-700 dark:text-X-300` (the CR-NS-067c convention) — the
// -300/-400 shades are too faint on the white light-theme surface, so light mode uses the darker -700.
// Yellow is the worst offender on white (design §4.5 calls it out): yellow-700 is a legible amber-brown
// on light, yellow-300 a bright gold on dark.
const EPIC_LEVEL_COLOR = "text-purple-700 dark:text-purple-300";
const FEAT_LEVEL_COLOR = "text-yellow-700 dark:text-yellow-300";
const TASK_LEVEL_COLOR = "text-blue-700 dark:text-blue-300";

// localStorage key for the collapsed-node set, scoped per version so each version's tree remembers its
// own expand/collapse state independently (per browser — OQ-8).
function collapsedStorageKey(versionId: string): string {
  return `nex_taskplan_collapsed_${versionId}`;
}

function readCollapsed(versionId: string): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(collapsedStorageKey(versionId));
    if (!raw) return new Set();
    const ids = JSON.parse(raw) as unknown;
    return Array.isArray(ids) ? new Set(ids.filter((id): id is string => typeof id === "string")) : new Set();
  } catch {
    return new Set();
  }
}

function writeCollapsed(versionId: string, collapsed: Set<string>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(collapsedStorageKey(versionId), JSON.stringify([...collapsed]));
  } catch {
    // Quota / disabled storage — the tree still works in-session, just doesn't persist.
  }
}

function StatusBadge({ status }: { status: string }) {
  // Dot colour from the unified palette (CR-NS-028): in_progress=blue, done=green, todo/planned=amber,
  // failed=red — never amber-for-in_progress.
  return (
    <span className="inline-flex flex-shrink-0 items-center gap-1 text-[10px] text-[var(--color-text-secondary)]">
      <span className={`h-1.5 w-1.5 rounded-full ${TONE_DOT[TASK_STATUS_TONE[status] ?? "neutral"]}`} />
      {TASK_STATUS_LABELS[status] ?? status}
    </span>
  );
}

// Roll a parent's DISPLAYED status UP from its descendant tasks (CR-NS-026). The DB feat.status /
// epic.status lag the build loop (the orchestrator recomputes feat status, but the tree fetch can be
// a beat behind), so WHEN there are children the badge is derived from them — always consistent with
// the tasks and the % indicator. Precedence: any in_progress wins, else any failed, else all-done,
// else the parent's resting label ("todo" for a feat, "planned" for an epic — epics use
// planned/in_progress/done). When there are NO tasks to derive from (a feat/epic not yet materialized,
// or genuinely empty), the children tell us nothing, so we fall back to the authoritative DB node
// status `dbStatus` rather than wrongly showing the resting label (review: a done node with an empty
// tasks array must not read as "todo").
function rollupStatus(tasks: TaskPlanTaskNode[], dbStatus: string, resting: string): string {
  if (tasks.length === 0) return dbStatus;
  if (tasks.some((t) => t.status === "in_progress")) return "in_progress";
  if (tasks.some((t) => t.status === "failed")) return "failed";
  if (tasks.every((t) => t.status === "done")) return "done";
  // Partially built (some done, none active) — e.g. a paused node with skeleton+auth done, rest todo —
  // reads as in_progress, NOT resting (CR-NS-028): any started work means it's underway. Only an
  // all-todo node stays at the resting label (truly not started).
  if (tasks.some((t) => t.status === "done")) return "in_progress";
  return resting;
}

export default function TaskPlanPanel({ versionId, messages }: Props) {
  const [plan, setPlan] = useState<TaskPlanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Expand/collapse state is hydrated from localStorage (per version, per browser — OQ-8) so it
  // survives navigation away+back AND a page reload. Re-init on version change so each version's tree
  // restores its own remembered state.
  const [collapsed, setCollapsed] = useState<Set<string>>(() => readCollapsed(versionId));
  useEffect(() => {
    setCollapsed(readCollapsed(versionId));
  }, [versionId]);

  // Tree-freshness (spec §7.1): refetch on mount, on version change, and whenever the live message
  // stream grows, so node statuses track the build loop without a new endpoint/WS field.
  // messages.length is the debounce key (one refetch per new message, not per render).
  useEffect(() => {
    let cancelled = false;
    getTaskPlan(versionId)
      .then((p) => {
        if (!cancelled) {
          setPlan(p);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Načítanie plánu zlyhalo");
      });
    return () => {
      cancelled = true;
    };
  }, [versionId, messages.length]);

  // Toggle a node's collapsed state and persist the new set to localStorage so the choice survives
  // navigation + reload (per version, per browser — OQ-8).
  const toggle = useCallback(
    (id: string) =>
      setCollapsed((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        writeCollapsed(versionId, next);
        return next;
      }),
    [versionId],
  );

  const allTasks: TaskPlanTaskNode[] = (plan?.plan ?? []).flatMap((e) => e.feats.flatMap((f) => f.tasks));

  // Build-progress indicator (CR-NS-025 Part 2): % of tasks done, live for free off the same fetched
  // plan as the tree (Part 1's task-start refetch updates it as tasks finish — no new data/endpoint).
  const doneCount = allTasks.filter((t) => t.status === "done").length;
  const failedCount = allTasks.filter((t) => t.status === "failed").length;
  const totalCount = allTasks.length;
  const pct = totalCount ? Math.round((doneCount / totalCount) * 100) : 0;
  // Show only when there are tasks to track and no fetch error: hides the no-plan / loading / error
  // states (mirrors the tree's error branch) AND the degenerate "epics but zero tasks" case — no
  // confusing "0/0 úloh (0 %)". totalCount>0 already implies a non-null plan with a populated tree.
  const showProgress = !error && totalCount > 0;

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-shrink-0 items-center justify-between border-b border-[var(--color-border-default)] px-3 py-2">
        <span className="text-xs font-semibold text-[var(--color-text-secondary)]">Plán úloh</span>
        {plan && (
          <span className="text-[10px] text-[var(--color-text-muted)]">
            {plan.epic_count} epic · {plan.feat_count} feat · {plan.task_count} úloh
          </span>
        )}
      </div>

      {showProgress && (
        <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-3 py-2.5">
          <div className="mb-1.5 flex items-baseline justify-between gap-2 text-[11px]">
            <span className="text-[var(--color-text-secondary)]">
              Stav: <span className="text-[var(--color-text-secondary)]">{doneCount}/{totalCount} úloh</span>
              {failedCount > 0 && <span className="font-medium text-[var(--color-status-error)]"> · {failedCount} zlyhané</span>}
            </span>
            <span className="font-semibold tabular-nums text-[var(--color-text-primary)]">{pct} %</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--color-surface-hover)]">
            <div
              data-testid="taskplan-progress-fill"
              // Always green (CR-NS-028): the fill shows completed progress, and green = done.
              className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-400 transition-[width] duration-500 ease-out"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2 text-xs">
        {error ? (
          <p className="px-1 text-[11px] text-[var(--color-status-error)]">{error}</p>
        ) : !plan ? (
          <p className="flex items-center gap-1.5 px-1 text-[var(--color-text-muted)]">
            <Loader2 className="h-3 w-3 animate-spin" /> Načítavam plán…
          </p>
        ) : plan.plan.length === 0 ? (
          <p className="px-1 text-[11px] text-[var(--color-text-muted)]">Plán úloh ešte nebol vytvorený.</p>
        ) : (
          plan.plan.map((epic: TaskPlanEpicNode) => {
            const epicCollapsed = collapsed.has(epic.id);
            const epicStatus = rollupStatus(
              epic.feats.flatMap((f) => f.tasks),
              epic.status,
              "planned",
            );
            return (
              <div key={epic.id} className="mb-1">
                <button
                  onClick={() => toggle(epic.id)}
                  className="flex w-full items-center justify-between gap-2 rounded px-1 py-1 text-left hover:bg-[var(--color-surface-hover)]"
                >
                  <span className={`flex min-w-0 items-center gap-1 ${EPIC_LEVEL_COLOR}`}>
                    {epicCollapsed ? <ChevronRight className="h-3 w-3 flex-shrink-0" /> : <ChevronDown className="h-3 w-3 flex-shrink-0" />}
                    <span className="truncate font-medium">
                      {epic.number}. {epic.title}
                    </span>
                  </span>
                  <StatusBadge status={epicStatus} />
                </button>

                {!epicCollapsed &&
                  epic.feats.map((feat: TaskPlanFeatNode) => {
                    const featCollapsed = collapsed.has(feat.id);
                    const featStatus = rollupStatus(feat.tasks, feat.status, "todo");
                    return (
                      <div key={feat.id} className="ml-3">
                        <button
                          onClick={() => toggle(feat.id)}
                          className="flex w-full items-center justify-between gap-2 rounded px-1 py-0.5 text-left hover:bg-[var(--color-surface-hover)]"
                        >
                          <span className={`flex min-w-0 items-center gap-1 ${FEAT_LEVEL_COLOR}`}>
                            {featCollapsed ? <ChevronRight className="h-3 w-3 flex-shrink-0" /> : <ChevronDown className="h-3 w-3 flex-shrink-0" />}
                            <span className="truncate">
                              {feat.number}. {feat.title}
                            </span>
                          </span>
                          <StatusBadge status={featStatus} />
                        </button>

                        {!featCollapsed &&
                          feat.tasks.map((task: TaskPlanTaskNode) => {
                            // Tasks are leaf rows — display only (v2 has no per-task audit panel to open).
                            // The in_progress task gets a subtle ring so the Manažér sees which one is underway.
                            const isCurrent = task.status === "in_progress";
                            return (
                              <div
                                key={task.id}
                                className={`ml-6 flex w-[calc(100%-1.5rem)] items-center justify-between gap-2 rounded px-1 py-0.5 ${
                                  isCurrent ? "ring-1 ring-[var(--color-status-info)]/40" : ""
                                }`}
                              >
                                <span className={`flex min-w-0 items-center gap-1.5 ${TASK_LEVEL_COLOR}`}>
                                  <span className="truncate">
                                    {task.number}. {task.title}
                                  </span>
                                  <span className="flex-shrink-0 text-[9px] uppercase text-[var(--color-text-muted)]">{task.task_type}</span>
                                </span>
                                <StatusBadge status={task.status} />
                              </div>
                            );
                          })}
                      </div>
                    );
                  })}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
