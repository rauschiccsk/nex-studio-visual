import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { getVersion } from "@/services/api/versions";
import { listDesignDocuments } from "@/services/api/designDocuments";
import { fetchTaskPlan, generateTaskPlan } from "@/services/api/taskPlan";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import { useActiveContextSync } from "@/hooks/useActiveContextSync";
import type { TaskPlanEpic, TaskPlanFeat, TaskPlanTask, TaskStatus } from "@/types/taskPlan";

// ─── TaskPlanPage — Step 6 ────────────────────────────────────────────────────

type PlanState = { plan: TaskPlanEpic[]; epic_count: number; feat_count: number; task_count: number } | null;

export default function TaskPlanPage() {
  const { slug, versionId } = useParams<{ slug: string; versionId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [hasDesignDocs, setHasDesignDocs] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [planState, setPlanState] = useState<PlanState>(null);
  const [generating, setGenerating] = useState(false);
  const [genProgress, setGenProgress] = useState({ message: "", percent: 0 });
  const [genError, setGenError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  // Collapsed state: epic ids that are open
  const [openEpics, setOpenEpics] = useState<Set<string>>(new Set());
  const [openFeats, setOpenFeats] = useState<Set<string>>(new Set());

  useActiveContextSync(project, version);

  useEffect(() => {
    if (!slug || !versionId) return;
    let cancelled = false;
    Promise.all([
      listProjectsApi({ limit: 100 }).then((res) => res.items.find((p) => p.slug === slug) ?? null),
      getVersion(versionId),
    ]).then(([proj, ver]) => {
      if (cancelled || !proj) { setError("Projekt nebol nájdený."); setLoading(false); return; }
      setProject(proj);
      setVersion(ver);
      return Promise.all([
        listDesignDocuments({ project_id: proj.id, doc_type: "behavior", limit: 1 }),
        listDesignDocuments({ project_id: proj.id, doc_type: "design", limit: 1 }),
        fetchTaskPlan(versionId!),
      ]).then(([behRes, desRes, plan]) => {
        if (cancelled) return;
        setHasDesignDocs(!!behRes.items[0] && !!desRes.items[0]);
        if (plan) {
          setPlanState(plan);
          // Open all epics by default
          setOpenEpics(new Set(plan.plan.map((e) => e.id)));
        }
        setLoading(false);
      });
    }).catch(() => { if (!cancelled) { setError("Nepodarilo sa načítať dáta."); setLoading(false); } });
    return () => { cancelled = true; };
  }, [slug, versionId]);

  function handleGenerate(replace = false) {
    if (!versionId || generating) return;
    setGenerating(true);
    setGenError("");
    setGenProgress({ message: "Spúšťam generáciu…", percent: 0 });
    abortRef.current = generateTaskPlan(
      versionId,
      replace,
      (message, percent) => setGenProgress({ message, percent }),
      (ev) => {
        setGenerating(false);
        setPlanState(ev);
        setOpenEpics(new Set(ev.plan.map((e) => e.id)));
      },
      (err) => { setGenerating(false); setGenError(err.message); },
      (reason) => { setGenerating(false); setGenError(`Validation error: ${reason}`); },
    );
  }

  function toggleEpic(id: string) {
    setOpenEpics((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleFeat(id: string) {
    setOpenFeats((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  if (loading) return <LoadingSpinner />;
  if (error || !project || !version) return <ErrorPanel msg={error} />;

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
        <span className="text-xs font-medium text-primary-400">Krok 6/7 — Task Plan</span>
        <div className="flex-1" />
        {planState && !generating && (
          <>
            <button
              onClick={() => handleGenerate(true)}
              className="text-[10px] text-slate-500 hover:text-slate-300 border border-slate-700 px-2 py-1 rounded transition-colors"
            >
              Regenerovať
            </button>
            <button
              onClick={() => navigate(`/projects/${slug}/versions/${versionId}/implementacia`)}
              className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
            >
              Krok 7 →
            </button>
          </>
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Gate: no design docs */}
        {!hasDesignDocs && (
          <div className="flex flex-col items-center justify-center h-full p-10 text-center">
            <svg className="w-12 h-12 text-slate-700 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
            <p className="text-sm text-slate-500 mb-1">Čaká na Architecture (Krok 4)</p>
            <p className="text-xs text-slate-700 mb-4">Task Plan vyžaduje vygenerované BEHAVIOR.md aj DESIGN.md.</p>
            <button
              onClick={() => navigate(`/projects/${slug}/versions/${versionId}/architecture`)}
              className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
            >
              ← Krok 4 — Architecture
            </button>
          </div>
        )}

        {/* Generate panel — no plan yet */}
        {hasDesignDocs && !planState && !generating && (
          <div className="p-6 max-w-3xl mx-auto">
            <div className="rounded-xl border border-slate-700 bg-slate-900 p-6 text-center space-y-4">
              <div className="w-12 h-12 rounded-full bg-primary-600/20 flex items-center justify-center mx-auto">
                <svg className="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                </svg>
              </div>
              <div>
                <p className="text-sm font-semibold text-slate-200 mb-1">Generuj Task Plan</p>
                <p className="text-xs text-slate-500">AI vygeneruje Epic → Feat → Task hierarchiu z DESIGN.md a BEHAVIOR.md.</p>
              </div>
              {genError && (
                <div className="rounded-lg bg-red-500/10 border border-red-500/20 p-3 text-xs text-red-400">{genError}</div>
              )}
              <button
                onClick={() => handleGenerate(false)}
                className="text-xs bg-primary-600 hover:bg-primary-500 text-white px-5 py-2 rounded-lg font-medium transition-colors"
              >
                Generovať Task Plan
              </button>
            </div>
          </div>
        )}

        {/* Generating — progress */}
        {generating && (
          <div className="p-6 max-w-3xl mx-auto space-y-4">
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <svg className="w-4 h-4 animate-spin text-primary-400" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <span className="flex-1 text-xs">{genProgress.message || "Generujem…"}</span>
              <button onClick={() => abortRef.current?.abort()} className="text-xs text-slate-600 hover:text-red-400 transition-colors">Zastaviť</button>
            </div>
            <div className="rounded-full bg-slate-800 h-2 overflow-hidden">
              <div
                className="h-full bg-primary-500 transition-all duration-300"
                style={{ width: `${genProgress.percent}%` }}
              />
            </div>
            <p className="text-[10px] text-slate-600 text-center">{genProgress.percent}% dokončené</p>
          </div>
        )}

        {/* Plan tree */}
        {planState && !generating && (
          <div className="p-6 max-w-5xl mx-auto space-y-4">
            {/* Stats */}
            <div className="flex items-center gap-4 mb-2">
              <StatPill label="Epics" value={planState.epic_count} color="text-primary-400" />
              <StatPill label="Feats" value={planState.feat_count} color="text-slate-300" />
              <StatPill label="Tasks" value={planState.task_count} color="text-slate-300" />
            </div>

            {genError && (
              <div className="rounded-lg bg-red-500/10 border border-red-500/20 p-3 text-xs text-red-400">{genError}</div>
            )}

            {planState.plan.map((epic) => (
              <EpicCard
                key={epic.id}
                epic={epic}
                open={openEpics.has(epic.id)}
                onToggle={() => toggleEpic(epic.id)}
                openFeats={openFeats}
                onToggleFeat={toggleFeat}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatPill({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center gap-1.5 rounded-full bg-slate-800 border border-slate-700 px-3 py-1">
      <span className={`text-sm font-bold ${color}`}>{value}</span>
      <span className="text-[10px] text-slate-500">{label}</span>
    </div>
  );
}

function EpicCard({ epic, open, onToggle, openFeats, onToggleFeat }: {
  epic: TaskPlanEpic;
  open: boolean;
  onToggle: () => void;
  openFeats: Set<string>;
  onToggleFeat: (id: string) => void;
}) {
  const doneFeats = epic.feats.filter((f) => f.status === "done").length;
  const totalFeats = epic.feats.length;

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900 overflow-hidden">
      {/* Epic header */}
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-slate-800/50 transition-colors text-left"
      >
        <div className={`w-7 h-7 rounded-lg flex items-center justify-center text-xs font-bold shrink-0 ${epicStatusCls(epic.status)}`}>
          {epic.number}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-slate-200 truncate">{epic.title}</span>
            <EpicStatusBadge status={epic.status} />
          </div>
          <div className="text-[10px] text-slate-600 mt-0.5">{doneFeats}/{totalFeats} feats</div>
        </div>
        <svg
          className={`w-4 h-4 text-slate-600 transition-transform flex-shrink-0 ${open ? "rotate-90" : ""}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
      </button>

      {/* Feats */}
      {open && (
        <div className="border-t border-slate-800 divide-y divide-slate-800/60">
          {epic.feats.map((feat) => (
            <FeatRow
              key={feat.id}
              feat={feat}
              open={openFeats.has(feat.id)}
              onToggle={() => onToggleFeat(feat.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function FeatRow({ feat, open, onToggle }: { feat: TaskPlanFeat; open: boolean; onToggle: () => void }) {
  const doneTasks = feat.tasks.filter((t) => t.status === "done").length;

  return (
    <div className="bg-slate-900/50">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-6 py-2.5 hover:bg-slate-800/30 transition-colors text-left"
      >
        <div className={`w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold shrink-0 ${featStatusCls(feat.status)}`}>
          {feat.number}
        </div>
        <span className="flex-1 text-xs text-slate-300 truncate">{feat.title}</span>
        <span className="text-[10px] text-slate-600 mr-1">{doneTasks}/{feat.tasks.length}</span>
        <FeatStatusBadge status={feat.status} />
        <svg
          className={`w-3.5 h-3.5 text-slate-700 transition-transform ml-1 flex-shrink-0 ${open ? "rotate-90" : ""}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
      </button>

      {open && feat.tasks.length > 0 && (
        <div className="pb-2">
          {feat.tasks.map((task) => (
            <TaskRow key={task.id} task={task} />
          ))}
        </div>
      )}
    </div>
  );
}

function TaskRow({ task }: { task: TaskPlanTask }) {
  return (
    <div className="flex items-center gap-3 px-10 py-1.5">
      <TaskStatusIcon status={task.status} />
      <span className={`text-[11px] flex-1 ${task.status === "done" ? "line-through text-slate-600" : "text-slate-400"}`}>
        {task.title}
      </span>
      <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded ${taskTypeCls(task.task_type)}`}>
        {task.task_type}
      </span>
      {task.priority !== "normal" && (
        <span className={`text-[9px] font-medium ${task.priority === "urgent" ? "text-red-400" : "text-yellow-400"}`}>
          {task.priority}
        </span>
      )}
    </div>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function epicStatusCls(status: string) {
  if (status === "done") return "bg-green-500 text-white";
  if (status === "in_progress") return "bg-primary-600 text-white ring-2 ring-primary-400/30";
  return "bg-slate-700 text-slate-400";
}

function EpicStatusBadge({ status }: { status: string }) {
  if (status === "in_progress") return <span className="text-[9px] bg-primary-500/15 text-primary-400 px-1.5 py-0.5 rounded-full">In Progress</span>;
  if (status === "done") return <span className="text-[9px] bg-green-500/15 text-green-400 px-1.5 py-0.5 rounded-full">Done</span>;
  return null;
}

function featStatusCls(status: string) {
  if (status === "done") return "bg-green-500/20 text-green-400";
  if (status === "in_progress") return "bg-primary-500/20 text-primary-400";
  if (status === "failed") return "bg-red-500/20 text-red-400";
  return "bg-slate-700/60 text-slate-500";
}

function FeatStatusBadge({ status }: { status: string }) {
  if (status === "done") return <span className="text-[9px] text-green-400">✓</span>;
  if (status === "failed") return <span className="text-[9px] text-red-400">✗</span>;
  if (status === "in_progress") return <span className="text-[9px] text-primary-400">▶</span>;
  return null;
}

function TaskStatusIcon({ status }: { status: TaskStatus }) {
  if (status === "done") return (
    <svg className="w-3.5 h-3.5 text-green-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
    </svg>
  );
  if (status === "failed") return (
    <svg className="w-3.5 h-3.5 text-red-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
  if (status === "in_progress") return (
    <div className="w-3.5 h-3.5 rounded-full border-2 border-primary-400 border-t-transparent animate-spin flex-shrink-0" />
  );
  return <div className="w-3.5 h-3.5 rounded-full border border-slate-700 flex-shrink-0" />;
}

function taskTypeCls(type: string) {
  const map: Record<string, string> = {
    backend: "bg-blue-500/15 text-blue-400",
    frontend: "bg-violet-500/15 text-violet-400",
    migration: "bg-orange-500/15 text-orange-400",
    test: "bg-cyan-500/15 text-cyan-400",
    docs: "bg-slate-700 text-slate-500",
  };
  return map[type] ?? "bg-slate-700 text-slate-500";
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
