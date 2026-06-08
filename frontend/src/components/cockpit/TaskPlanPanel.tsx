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
import { TASK_STATUS_LABELS } from "./labels";

interface Props {
  versionId: string;
  /** Live message stream (board.recent_messages) — the audit panel reads from it. */
  messages: PipelineMessage[];
}

const STATUS_DOT: Record<string, string> = {
  planned: "bg-slate-500",
  todo: "bg-slate-500",
  in_progress: "bg-amber-400",
  done: "bg-emerald-500",
  failed: "bg-red-500",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span className="inline-flex flex-shrink-0 items-center gap-1 text-[10px] text-slate-400">
      <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[status] ?? "bg-slate-600"}`} />
      {TASK_STATUS_LABELS[status] ?? status}
    </span>
  );
}

interface AuditView {
  task_pass?: boolean;
  findings?: string[];
}

// Read the per-task audit verdict (latest Auditor turn) + auto-fix reasons for a task from the
// live message stream — matched by payload.task_id, scoped to build/gate_g (spec §7.1).
function readTaskAudit(messages: PipelineMessage[], taskId: string): { audit?: AuditView; reasons: string[] } {
  const forTask = messages.filter((m) => {
    const p = m.payload as { task_id?: string } | null;
    return p?.task_id === taskId && (m.stage === "build" || m.stage === "gate_g");
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
      <div className="flex flex-shrink-0 items-center justify-between border-b border-slate-800 px-3 py-2">
        <span className="text-xs font-semibold text-slate-300">Plán úloh</span>
        {plan && (
          <span className="text-[10px] text-slate-500">
            {plan.epic_count} epic · {plan.feat_count} feat · {plan.task_count} úloh
          </span>
        )}
      </div>

      {showProgress && (
        <div className="flex-shrink-0 border-b border-slate-800 px-3 py-2">
          <div className="mb-1 text-[10px] text-slate-400">
            <span>
              Postup: {doneCount}/{totalCount} úloh ({pct} %)
            </span>
            {failedCount > 0 && <span className="text-red-400"> · {failedCount} zlyhané</span>}
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
            <div
              data-testid="taskplan-progress-fill"
              className={`h-full rounded-full transition-all ${pct === 100 ? "bg-emerald-500" : "bg-amber-400"}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2 text-xs">
        {error ? (
          <p className="px-1 text-[11px] text-red-400">{error}</p>
        ) : !plan ? (
          <p className="flex items-center gap-1.5 px-1 text-slate-600">
            <Loader2 className="h-3 w-3 animate-spin" /> Načítavam plán…
          </p>
        ) : plan.plan.length === 0 ? (
          <p className="px-1 text-[11px] text-slate-600">Plán úloh ešte nebol vytvorený.</p>
        ) : (
          plan.plan.map((epic: TaskPlanEpicNode) => {
            const epicCollapsed = collapsed.has(epic.id);
            return (
              <div key={epic.id} className="mb-1">
                <button
                  onClick={() => toggle(epic.id)}
                  className="flex w-full items-center justify-between gap-2 rounded px-1 py-1 text-left hover:bg-slate-800/60"
                >
                  <span className="flex min-w-0 items-center gap-1 text-slate-200">
                    {epicCollapsed ? <ChevronRight className="h-3 w-3 flex-shrink-0" /> : <ChevronDown className="h-3 w-3 flex-shrink-0" />}
                    <span className="truncate font-medium">
                      {epic.number}. {epic.title}
                    </span>
                  </span>
                  <StatusBadge status={epic.status} />
                </button>

                {!epicCollapsed &&
                  epic.feats.map((feat: TaskPlanFeatNode) => {
                    const featCollapsed = collapsed.has(feat.id);
                    return (
                      <div key={feat.id} className="ml-3">
                        <button
                          onClick={() => toggle(feat.id)}
                          className="flex w-full items-center justify-between gap-2 rounded px-1 py-0.5 text-left hover:bg-slate-800/60"
                        >
                          <span className="flex min-w-0 items-center gap-1 text-slate-300">
                            {featCollapsed ? <ChevronRight className="h-3 w-3 flex-shrink-0" /> : <ChevronDown className="h-3 w-3 flex-shrink-0" />}
                            <span className="truncate">
                              {feat.number}. {feat.title}
                            </span>
                          </span>
                          <StatusBadge status={feat.status} />
                        </button>

                        {!featCollapsed &&
                          feat.tasks.map((task: TaskPlanTaskNode) => {
                            const isCurrent = task.status === "in_progress";
                            const isSelected = task.id === selectedTaskId;
                            return (
                              <button
                                key={task.id}
                                onClick={() => setSelectedTaskId(isSelected ? null : task.id)}
                                className={`ml-6 flex w-[calc(100%-1.5rem)] items-center justify-between gap-2 rounded px-1 py-0.5 text-left hover:bg-slate-800/60 ${
                                  isSelected ? "bg-slate-800" : ""
                                } ${isCurrent ? "ring-1 ring-amber-400/40" : ""}`}
                              >
                                <span className="flex min-w-0 items-center gap-1.5 text-slate-400">
                                  <span className="truncate">
                                    {task.number}. {task.title}
                                  </span>
                                  <span className="flex-shrink-0 text-[9px] uppercase text-slate-600">{task.task_type}</span>
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
    <div className="flex-shrink-0 border-t border-slate-800 px-3 py-2 text-xs">
      <div className="mb-1 flex items-center justify-between">
        <span className="truncate font-medium text-slate-300">
          Audit — {task.number}. {task.title}
        </span>
        <StatusBadge status={task.status} />
      </div>
      {!audit ? (
        <p className="text-[11px] text-slate-600">Úloha ešte nebola auditovaná.</p>
      ) : (
        <p className={`text-[11px] ${audit.task_pass ? "text-emerald-400" : "text-red-400"}`}>
          {audit.task_pass ? "Audit PASS" : "Audit FAIL"}
        </p>
      )}
      {audit?.findings && audit.findings.length > 0 && (
        <ul className="mt-1 list-disc pl-4 text-[11px] text-slate-400">
          {audit.findings.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}
      {reasons.length > 0 && (
        <div className="mt-1.5">
          <span className="text-[10px] uppercase tracking-wide text-slate-600">Auto-fix dôvody</span>
          <ul className="mt-0.5 list-disc pl-4 text-[11px] text-slate-500">
            {reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
