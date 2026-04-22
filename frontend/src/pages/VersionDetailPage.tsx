import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { getVersion } from "@/services/api/versions";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";

// ─── Pipeline step definitions ────────────────────────────────────────────────

interface PipelineStep {
  n: number;
  label: string;
  sublabel: string;
  waitFor?: string;
}

const PIPELINE_STEPS: PipelineStep[] = [
  { n: 1, label: "Zákaznícka špecifikácia", sublabel: "Zákazník opisuje čo chce v tejto verzii. Pre v0.2+ stačí delta." },
  { n: 2, label: "Vývojová dokumentácia", sublabel: "Paralelná fáza: BEHAVIOR.md + DESIGN.md — architektonické rozhodnutia." },
  { n: 3, label: "Súhrnná dokumentácia", sublabel: "Professional Spec — konsolidácia dokumentácie.", waitFor: "Vývojová dokumentácia" },
  { n: 4, label: "Architecture", sublabel: "BEHAVIOR.md + DESIGN.md review a finalizácia.", waitFor: "Súhrnná dokumentácia" },
  { n: 5, label: "Quality Audit", sublabel: "Nezávislá kontrola — súlad dokumentácie, medzery, ICC štandardy.", waitFor: "Architecture" },
  { n: 6, label: "Task Plan", sublabel: "Epic → Feat → Task breakdown, CC delegácia pripravená.", waitFor: "Quality Audit" },
  { n: 7, label: "Implementácia", sublabel: "CC agent píše kód, testy, commity — automatizované.", waitFor: "Task Plan" },
];

// ─── Status helpers ───────────────────────────────────────────────────────────

type StepState = "done" | "active" | "pending";

function computeStepStates(version: Version): StepState[] {
  // Heuristic: epics_done / max(epic_count,1) → which steps are done
  // For a brand new version (0 epics), only step 1 is "active"
  const total = version.epic_count || 0;
  const done = version.epics_done || 0;
  const activePct = total === 0 ? 0 : done / total;
  // Map 0..1 to 0..7 done steps
  const doneSteps = total === 0 ? 0 : Math.min(Math.floor(activePct * 7), 7);

  return PIPELINE_STEPS.map((_, i) => {
    const n = i + 1;
    if (n <= doneSteps) return "done";
    if (n === doneSteps + 1) return "active";
    return "pending";
  });
}

function versionStatusCls(status: string) {
  if (status === "active") return "bg-yellow-500/15 border border-yellow-500/30 text-yellow-400";
  if (status === "released") return "bg-green-500/10 border border-green-500/25 text-green-400";
  return "bg-slate-700/60 border border-slate-600 text-slate-400";
}

function versionStatusLabel(status: string) {
  if (status === "active") return "In Progress";
  if (status === "released") return "Released";
  return "Planned";
}

// ─── Mini pipeline bar ────────────────────────────────────────────────────────

function MiniPipelineBar({ states }: { states: StepState[] }) {
  return (
    <div className="flex items-center gap-1 px-5 pb-2">
      {states.map((s, i) => {
        let cls = "h-1.5 flex-1 rounded-full ";
        if (s === "done") cls += "bg-green-500";
        else if (s === "active") cls += "bg-primary-500 ring-1 ring-primary-400/40";
        else cls += "bg-slate-700";
        return <div key={i} className={cls} />;
      })}
    </div>
  );
}

// ─── Step card ────────────────────────────────────────────────────────────────

function StepCard({ step, state }: { step: PipelineStep; state: StepState }) {
  const isDone = state === "done";
  const isActive = state === "active";
  const isPending = state === "pending";

  return (
    <div
      className={`rounded-xl border p-4 flex items-center gap-4 transition-all ${
        isDone
          ? "border-green-500/20 bg-slate-900"
          : isActive
          ? "border-primary-500/40 bg-slate-900"
          : "border-slate-800 bg-slate-900 opacity-50"
      }`}
    >
      {/* Step circle */}
      <div
        className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold shrink-0 ${
          isDone
            ? "bg-green-500 text-white"
            : isActive
            ? "bg-primary-600 ring-4 ring-primary-500/25 text-white"
            : "bg-slate-700 text-slate-500"
        }`}
      >
        {isDone ? "✓" : step.n}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span
            className={`text-xs font-semibold uppercase tracking-wider ${
              isDone ? "text-green-500" : isActive ? "text-primary-400" : "text-slate-600"
            }`}
          >
            {isDone
              ? `Krok ${step.n} · Hotový`
              : isActive
              ? `Krok ${step.n} · Aktívny`
              : `Krok ${step.n}`}
          </span>
        </div>
        <div className={`text-sm font-semibold ${isDone ? "text-slate-100" : isActive ? "text-slate-100" : "text-slate-400"}`}>
          {step.label}
        </div>
        <div className="text-xs text-slate-500 mt-0.5 truncate">{step.sublabel}</div>
      </div>

      {/* Right side */}
      <div className="shrink-0">
        {isDone && (
          <span className="text-[10px] bg-green-500/10 border border-green-500/25 text-green-400 px-2 py-0.5 rounded-full">
            Schválené
          </span>
        )}
        {isActive && (
          <button className="text-[10px] bg-primary-600 hover:bg-primary-500 text-white px-3 py-1.5 rounded-lg font-medium transition-colors">
            Otvoriť →
          </button>
        )}
        {isPending && step.waitFor && (
          <span className="text-[10px] bg-slate-800 text-slate-600 px-2 py-0.5 rounded-full border border-slate-700">
            Čaká na {step.waitFor}
          </span>
        )}
      </div>
    </div>
  );
}

// ─── VersionDetailPage ────────────────────────────────────────────────────────

export default function VersionDetailPage() {
  const { slug, versionId } = useParams<{ slug: string; versionId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!slug || !versionId) return;
    let cancelled = false;

    Promise.all([
      listProjectsApi({ limit: 100 }).then((res) => res.items.find((p) => p.slug === slug) ?? null),
      getVersion(versionId),
    ])
      .then(([proj, ver]) => {
        if (cancelled) return;
        if (!proj) { setError("Projekt nebol nájdený."); return; }
        setProject(proj);
        setVersion(ver);
      })
      .catch(() => { if (!cancelled) setError("Nepodarilo sa načítať dáta."); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [slug, versionId]);

  if (loading) {
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

  if (error || !project || !version) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <div className="rounded-lg bg-red-500/10 border border-red-500/30 p-4 text-sm text-red-400">
          {error || "Verzia nebola nájdená."}
        </div>
      </div>
    );
  }

  const stepStates = computeStepStates(version);
  const doneCount = stepStates.filter((s) => s === "done").length;
  const activeStep = PIPELINE_STEPS[stepStates.findIndex((s) => s === "active")];
  const pct = Math.round((doneCount / 7) * 100);

  return (
    <div className="flex flex-col h-full">
      {/* ── Header ── */}
      <div className="border-b border-slate-800 bg-slate-900/60 shrink-0">
        {/* Top row */}
        <div className="flex items-center gap-3 px-5 py-2.5">
          <button
            onClick={() => navigate(`/projects/${slug}`)}
            className="text-slate-500 hover:text-slate-300 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-slate-400 font-medium">{project.name}</span>
            <span className="text-slate-600">·</span>
            <span className="bg-slate-800 text-slate-300 font-mono px-2 py-0.5 rounded">
              {version.version_number}
            </span>
            {version.name && (
              <span className="text-slate-400">{version.name}</span>
            )}
          </div>
          <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${versionStatusCls(version.status)}`}>
            {versionStatusLabel(version.status)}
          </span>
          <div className="flex-1" />
          {/* Stats */}
          <div className="flex items-center gap-5 text-center">
            <div>
              <div className="text-sm font-bold text-slate-100">{doneCount}/7</div>
              <div className="text-[10px] text-slate-500">steps</div>
            </div>
            <div>
              <div className="text-sm font-bold text-primary-400">{pct}%</div>
              <div className="text-[10px] text-slate-500">done</div>
            </div>
            <div>
              <div className="text-sm font-bold text-slate-100">{version.epic_count}</div>
              <div className="text-[10px] text-slate-500">epics</div>
            </div>
            <div>
              <div className={`text-sm font-bold ${version.bug_count > 0 ? "text-red-400" : "text-slate-100"}`}>
                {version.bug_count}
              </div>
              <div className="text-[10px] text-slate-500">bugs</div>
            </div>
          </div>
        </div>

        {/* Pipeline mini bar */}
        <MiniPipelineBar states={stepStates} />

        {/* Bar labels */}
        <div className="flex justify-between px-5 pb-2">
          <span className="text-[10px] text-slate-600">Raw Spec</span>
          {activeStep && (
            <span className="text-[10px] text-primary-400 font-medium">{activeStep.label}</span>
          )}
          <span className="text-[10px] text-slate-600">Implementácia</span>
        </div>
      </div>

      {/* ── Pipeline steps hub ── */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-3xl mx-auto space-y-3">
          {PIPELINE_STEPS.map((step, i) => (
            <StepCard key={step.n} step={step} state={stepStates[i] ?? "pending"} />
          ))}
        </div>
      </div>
    </div>
  );
}
