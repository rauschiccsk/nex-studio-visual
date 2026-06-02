import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { getVersion } from "@/services/api/versions";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import { useActiveContextSync } from "@/hooks/useActiveContextSync";

// ─── Status helpers ───────────────────────────────────────────────────────────

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

// ─── VersionDetailPage ────────────────────────────────────────────────────────

export default function VersionDetailPage() {
  const { slug, versionId } = useParams<{ slug: string; versionId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useActiveContextSync(project, version);

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

  const epicCount = version.epic_count ?? 0;
  const epicsDone = version.epics_done ?? 0;

  return (
    <div className="flex flex-col h-full">
      {/* ── Header ── */}
      <div className="border-b border-slate-800 bg-slate-900/60 shrink-0">
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
              <div className="text-sm font-bold text-slate-100">{epicsDone}/{epicCount}</div>
              <div className="text-[10px] text-slate-500">epics done</div>
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
      </div>

      {/* ── Body ── */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-3xl mx-auto">
          <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
            <div className="text-sm font-semibold text-slate-100 mb-1">
              {version.version_number}{version.name ? ` — ${version.name}` : ""}
            </div>
            <div className="text-xs text-slate-500">
              {epicCount} epic(s) · {epicsDone} done · {version.bug_count} bug(s).
              Epics, feats a tasks pre túto verziu spravujú agenti (Designer / Implementer / Auditor).
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
