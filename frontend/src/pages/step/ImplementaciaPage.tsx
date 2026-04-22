import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { getVersion } from "@/services/api/versions";
import { fetchTaskPlan, executeFeat } from "@/services/api/taskPlan";
import type { FeatExecuteEvent } from "@/services/api/taskPlan";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import type { TaskPlanEpic, TaskPlanFeat, TaskStatus } from "@/types/taskPlan";

// ─── ImplementaciaPage — Step 7 ───────────────────────────────────────────────

interface LogEntry {
  type: "task_start" | "chunk" | "task_done" | "feat_done" | "error" | "info";
  text: string;
  taskId?: string;
}

export default function ImplementaciaPage() {
  const { slug, versionId } = useParams<{ slug: string; versionId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [epics, setEpics] = useState<TaskPlanEpic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [openEpics, setOpenEpics] = useState<Set<string>>(new Set());
  const [executingFeat, setExecutingFeat] = useState<string | null>(null);
  const [taskStatuses, setTaskStatuses] = useState<Record<string, TaskStatus>>({});
  const [log, setLog] = useState<LogEntry[]>([]);
  const [currentTaskId, setCurrentTaskId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!slug || !versionId) return;
    let cancelled = false;
    Promise.all([
      listProjectsApi({ limit: 100 }).then((res) => res.items.find((p) => p.slug === slug) ?? null),
      getVersion(versionId),
      fetchTaskPlan(versionId!),
    ]).then(([proj, ver, plan]) => {
      if (cancelled || !proj) { setError("Projekt nebol nájdený."); setLoading(false); return; }
      setProject(proj);
      setVersion(ver);
      if (plan) {
        setEpics(plan.plan);
        setOpenEpics(new Set(plan.plan.map((e) => e.id)));
      }
      setLoading(false);
    }).catch(() => { if (!cancelled) { setError("Nepodarilo sa načítať dáta."); setLoading(false); } });
    return () => { cancelled = true; };
  }, [slug, versionId]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log]);

  function toggleEpic(id: string) {
    setOpenEpics((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function handleExecuteFeat(feat: TaskPlanFeat) {
    if (executingFeat) return;
    setExecutingFeat(feat.id);
    setCurrentTaskId(null);
    setLog((prev) => [...prev, { type: "info", text: `▶ Spúšťam feat: ${feat.title}` }]);

    abortRef.current = executeFeat(
      feat.id,
      (event: FeatExecuteEvent) => {
        switch (event.type) {
          case "task_start":
            setCurrentTaskId(event.task_id);
            setTaskStatuses((prev) => ({ ...prev, [event.task_id]: "in_progress" }));
            setLog((prev) => [...prev, {
              type: "task_start",
              text: `Task #${event.task_number}: ${event.task_title}`,
              taskId: event.task_id,
            }]);
            break;
          case "chunk":
            setLog((prev) => {
              // Append to last chunk entry if same task, else add new
              const last = prev[prev.length - 1];
              if (last && last.type === "chunk" && last.taskId === event.task_id) {
                return [...prev.slice(0, -1), { ...last, text: last.text + event.text }];
              }
              return [...prev, { type: "chunk", text: event.text, taskId: event.task_id }];
            });
            break;
          case "task_done":
            setTaskStatuses((prev) => ({ ...prev, [event.task_id]: event.status as TaskStatus }));
            setCurrentTaskId(null);
            break;
          case "feat_done":
            setExecutingFeat(null);
            setLog((prev) => [...prev, { type: "feat_done", text: `✓ Feat dokončený (${event.feat_status})` }]);
            // Reload task plan to get updated statuses
            if (versionId) {
              fetchTaskPlan(versionId).then((plan) => {
                if (plan) setEpics(plan.plan);
              });
            }
            break;
          case "error":
            setExecutingFeat(null);
            setLog((prev) => [...prev, { type: "error", text: `✗ Chyba: ${event.content}` }]);
            break;
        }
      },
      (err) => {
        setExecutingFeat(null);
        setLog((prev) => [...prev, { type: "error", text: `✗ ${err.message}` }]);
      },
    );
  }

  if (loading) return <LoadingSpinner />;
  if (error || !project || !version) return <ErrorPanel msg={error} />;

  const hasEpics = epics.length > 0;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex-shrink-0 bg-slate-900/50 border-b border-slate-800 px-5 py-2.5 flex items-center gap-3">
        <button onClick={() => navigate(`/projects/${slug}/versions/${versionId}`)} className="text-slate-500 hover:text-slate-300 transition-colors">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <span className="text-xs text-slate-400">{project.name}</span>
        <span className="text-slate-600">·</span>
        <span className="text-xs font-mono bg-slate-800 text-slate-300 px-2 py-0.5 rounded">{version.version_number}</span>
        <span className="text-slate-600">·</span>
        <span className="text-xs font-medium text-primary-400">Krok 7/7 — Implementácia</span>
        <div className="flex-1" />
        {executingFeat && (
          <button
            onClick={() => { abortRef.current?.abort(); setExecutingFeat(null); }}
            className="text-xs text-red-400 hover:text-red-300 border border-red-500/30 px-3 py-1 rounded transition-colors"
          >
            Zastaviť
          </button>
        )}
      </div>

      {/* Gate */}
      {!hasEpics && (
        <div className="flex flex-col items-center justify-center flex-1 p-10 text-center">
          <svg className="w-12 h-12 text-slate-700 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
          </svg>
          <p className="text-sm text-slate-500 mb-1">Čaká na Task Plan (Krok 6)</p>
          <p className="text-xs text-slate-700 mb-4">Implementácia vyžaduje vygenerovaný Task Plan s epicmi.</p>
          <button
            onClick={() => navigate(`/projects/${slug}/versions/${versionId}/taskplan`)}
            className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
          >
            ← Krok 6 — Task Plan
          </button>
        </div>
      )}

      {/* Split panel */}
      {hasEpics && (
        <div className="flex-1 overflow-hidden flex">
          {/* Left: Task tree */}
          <div className="w-80 flex-shrink-0 flex flex-col border-r border-slate-800 overflow-hidden">
            <div className="px-4 py-2.5 border-b border-slate-800 flex-shrink-0">
              <div className="text-xs font-semibold text-slate-400">Task Plan</div>
              <div className="text-[10px] text-slate-600">Klikni Execute na feat pre spustenie</div>
            </div>
            <div className="flex-1 overflow-y-auto">
              {epics.map((epic) => (
                <div key={epic.id}>
                  {/* Epic row */}
                  <button
                    onClick={() => toggleEpic(epic.id)}
                    className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-800/40 transition-colors text-left border-b border-slate-800/50"
                  >
                    <div className={`w-5 h-5 rounded text-[10px] font-bold flex items-center justify-center flex-shrink-0 ${epicStatusMini(epic.status)}`}>
                      {epic.number}
                    </div>
                    <span className="flex-1 text-xs font-medium text-slate-300 truncate">{epic.title}</span>
                    <svg
                      className={`w-3 h-3 text-slate-600 transition-transform flex-shrink-0 ${openEpics.has(epic.id) ? "rotate-90" : ""}`}
                      fill="none" stroke="currentColor" viewBox="0 0 24 24"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                  </button>

                  {/* Feats */}
                  {openEpics.has(epic.id) && epic.feats.map((feat) => (
                    <div key={feat.id} className="border-b border-slate-800/30">
                      {/* Feat header */}
                      <div className={`flex items-center gap-2 pl-7 pr-3 py-1.5 ${executingFeat === feat.id ? "bg-primary-900/20" : ""}`}>
                        <div className={`w-4 h-4 rounded text-[9px] font-bold flex items-center justify-center flex-shrink-0 ${featStatusMini(feat.status)}`}>
                          {feat.number}
                        </div>
                        <span className={`flex-1 text-[11px] truncate ${feat.status === "done" ? "line-through text-slate-600" : "text-slate-400"}`}>
                          {feat.title}
                        </span>
                        {feat.status !== "done" && (
                          <button
                            onClick={() => handleExecuteFeat(feat)}
                            disabled={!!executingFeat}
                            className={`text-[9px] px-1.5 py-0.5 rounded font-medium transition-colors flex-shrink-0 ${
                              executingFeat === feat.id
                                ? "bg-primary-600/30 text-primary-400"
                                : "bg-slate-700 text-slate-400 hover:bg-primary-600 hover:text-white disabled:opacity-40"
                            }`}
                          >
                            {executingFeat === feat.id ? "▶ Run" : "Execute"}
                          </button>
                        )}
                        {feat.status === "done" && <span className="text-[9px] text-green-400">✓</span>}
                      </div>

                      {/* Tasks */}
                      {feat.tasks.map((task) => {
                        const liveStatus = taskStatuses[task.id] ?? task.status;
                        return (
                          <div key={task.id} className="flex items-center gap-2 pl-12 pr-3 py-1">
                            <MiniTaskIcon status={liveStatus} active={currentTaskId === task.id} />
                            <span className={`text-[10px] truncate flex-1 ${liveStatus === "done" ? "line-through text-slate-600" : "text-slate-500"}`}>
                              {task.title}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>

          {/* Right: Execution log */}
          <div className="flex-1 flex flex-col overflow-hidden">
            <div className="px-4 py-2.5 border-b border-slate-800 flex-shrink-0 flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-widest">Execution Log</span>
              {log.length > 0 && (
                <button onClick={() => setLog([])} className="text-[10px] text-slate-600 hover:text-slate-400 transition-colors">
                  Vymazať
                </button>
              )}
            </div>
            <div className="flex-1 overflow-y-auto p-4 font-mono">
              {log.length === 0 && (
                <div className="text-xs text-slate-700 text-center py-10">
                  Spusti feat pre zobrazenie výstupu…
                </div>
              )}
              {log.map((entry, i) => (
                <LogLine key={i} entry={entry} />
              ))}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Log line ─────────────────────────────────────────────────────────────────

function LogLine({ entry }: { entry: LogEntry }) {
  if (entry.type === "task_start") {
    return (
      <div className="text-[11px] text-primary-400 mt-3 mb-1 border-t border-slate-800 pt-2">
        ▶ {entry.text}
      </div>
    );
  }
  if (entry.type === "feat_done") {
    return <div className="text-[11px] text-green-400 mt-2 mb-1">{entry.text}</div>;
  }
  if (entry.type === "error") {
    return <div className="text-[11px] text-red-400 mt-2">{entry.text}</div>;
  }
  if (entry.type === "info") {
    return <div className="text-[11px] text-slate-500 mb-1">{entry.text}</div>;
  }
  // chunk
  return (
    <pre className="text-[11px] text-slate-400 whitespace-pre-wrap leading-relaxed">{entry.text}</pre>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function epicStatusMini(status: string) {
  if (status === "done") return "bg-green-500 text-white";
  if (status === "in_progress") return "bg-primary-600 text-white";
  return "bg-slate-700 text-slate-400";
}

function featStatusMini(status: string) {
  if (status === "done") return "bg-green-500/20 text-green-400";
  if (status === "in_progress") return "bg-primary-500/20 text-primary-400";
  if (status === "failed") return "bg-red-500/20 text-red-400";
  return "bg-slate-700/60 text-slate-500";
}

function MiniTaskIcon({ status, active }: { status: TaskStatus; active: boolean }) {
  if (active) return <div className="w-3 h-3 rounded-full border-2 border-primary-400 border-t-transparent animate-spin flex-shrink-0" />;
  if (status === "done") return (
    <svg className="w-3 h-3 text-green-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
    </svg>
  );
  if (status === "failed") return (
    <svg className="w-3 h-3 text-red-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
  return <div className="w-3 h-3 rounded-full border border-slate-700 flex-shrink-0" />;
}

function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center py-20 text-slate-500 text-sm gap-2">
      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      Načítavam…
    </div>
  );
}

function ErrorPanel({ msg }: { msg: string }) {
  return (
    <div className="p-6 max-w-3xl mx-auto">
      <div className="rounded-lg bg-red-500/10 border border-red-500/30 p-4 text-sm text-red-400">
        {msg || "Nepodarilo sa načítať dáta."}
      </div>
    </div>
  );
}
