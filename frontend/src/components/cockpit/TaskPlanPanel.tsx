// Task-plan tree + per-task audit panel (F-007 §7, CR-NS-020 CR-5). Right column of the
// cockpit during task_plan/build: the Director sees the EPIC→FEAT→TASK decomposition with
// live per-node status, which task the Programmer is on, and (on click) the per-task audit
// verdict + findings read from the live PipelineMessage stream.

import { useEffect, useState } from "react";
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
  /** Live message stream (board.recent_messages) — the audit panel reads from it. */
  messages: PipelineMessage[];
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

interface AuditView {
  task_pass?: boolean;
  findings?: string[];
}

// Read the per-task audit verdict (latest Auditor turn) + auto-fix reasons for a task from the
// live message stream — matched by payload.task_id, scoped to the Programovanie phase.
// NOTE: v2 has NO per-task Auditor (design §2.2); this per-task audit view + readTaskAudit are RETIRED by
// CR-V2-023. The stage filter is re-pointed to the v2 ``programovanie`` phase only to keep it compiling
// against the 4-phase enum until CR-V2-023 removes it.
function readTaskAudit(messages: PipelineMessage[], taskId: string): { audit?: AuditView; reasons: string[] } {
  const forTask = messages.filter((m) => {
    const p = m.payload as { task_id?: string } | null;
    return p?.task_id === taskId && m.stage === "programovanie";
  });
  const auditorMsgs = forTask.filter((m) => m.author === "auditor");
  const latest = auditorMsgs[auditorMsgs.length - 1];
  const audit = latest
    ? {
        task_pass: (latest.payload as { task_pass?: boolean }).task_pass,
        findings: (latest.payload as { findings?: string[] }).findings,
      }
    : undefined;
  const reasons = forTask
    .filter((m) => m.author === "system" && m.kind === "return")
    .map((m) => (m.payload as { verify_reason?: string }).verify_reason)
    .filter((r): r is string => Boolean(r));
  return { audit, reasons };
}

export default function TaskPlanPanel({ versionId, messages }: Props) {
  const [plan, setPlan] = useState<TaskPlanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);

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

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const allTasks: TaskPlanTaskNode[] = (plan?.plan ?? []).flatMap((e) => e.feats.flatMap((f) => f.tasks));
  const selectedTask = allTasks.find((t) => t.id === selectedTaskId) ?? null;

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
                  <span className="flex min-w-0 items-center gap-1 text-[var(--color-text-primary)]">
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
                          <span className="flex min-w-0 items-center gap-1 text-[var(--color-text-secondary)]">
                            {featCollapsed ? <ChevronRight className="h-3 w-3 flex-shrink-0" /> : <ChevronDown className="h-3 w-3 flex-shrink-0" />}
                            <span className="truncate">
                              {feat.number}. {feat.title}
                            </span>
                          </span>
                          <StatusBadge status={featStatus} />
                        </button>

                        {!featCollapsed &&
                          feat.tasks.map((task: TaskPlanTaskNode) => {
                            const isCurrent = task.status === "in_progress";
                            const isSelected = task.id === selectedTaskId;
                            return (
                              <button
                                key={task.id}
                                onClick={() => setSelectedTaskId(isSelected ? null : task.id)}
                                className={`ml-6 flex w-[calc(100%-1.5rem)] items-center justify-between gap-2 rounded px-1 py-0.5 text-left hover:bg-[var(--color-surface-hover)] ${
                                  isSelected ? "bg-[var(--color-surface-active)]" : ""
                                } ${isCurrent ? "ring-1 ring-[var(--color-status-info)]/40" : ""}`}
                              >
                                <span className="flex min-w-0 items-center gap-1.5 text-[var(--color-text-secondary)]">
                                  <span className="truncate">
                                    {task.number}. {task.title}
                                  </span>
                                  <span className="flex-shrink-0 text-[9px] uppercase text-[var(--color-text-muted)]">{task.task_type}</span>
                                </span>
                                <StatusBadge status={task.status} />
                              </button>
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

      {selectedTask && <TaskAuditPanel task={selectedTask} messages={messages} />}
    </div>
  );
}

function TaskAuditPanel({ task, messages }: { task: TaskPlanTaskNode; messages: PipelineMessage[] }) {
  const { audit, reasons } = readTaskAudit(messages, task.id);
  return (
    <div className="flex-shrink-0 border-t border-[var(--color-border-default)] px-3 py-2 text-xs">
      <div className="mb-1 flex items-center justify-between">
        <span className="truncate font-medium text-[var(--color-text-secondary)]">
          Audit — {task.number}. {task.title}
        </span>
        <StatusBadge status={task.status} />
      </div>
      {!audit ? (
        <p className="text-[11px] text-[var(--color-text-muted)]">Úloha ešte nebola auditovaná.</p>
      ) : (
        <p className={`text-[11px] ${audit.task_pass ? "text-[var(--color-status-success)]" : "text-[var(--color-status-error)]"}`}>
          {audit.task_pass ? "Audit PASS" : "Audit FAIL"}
        </p>
      )}
      {audit?.findings && audit.findings.length > 0 && (
        <ul className="mt-1 list-disc pl-4 text-[11px] text-[var(--color-text-secondary)]">
          {audit.findings.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}
      {reasons.length > 0 && (
        <div className="mt-1.5">
          <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">Auto-fix dôvody</span>
          <ul className="mt-0.5 list-disc pl-4 text-[11px] text-[var(--color-text-muted)]">
            {reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
