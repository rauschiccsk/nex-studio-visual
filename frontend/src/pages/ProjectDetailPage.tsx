import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { listVersions } from "@/services/api/versions";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";

// ─── Pipeline bar ─────────────────────────────────────────────────────────────

const STEPS = 7;

function PipelineBar({ version }: { version: Version }) {
  const done = Math.min(version.epics_done, STEPS);
  return (
    <div className="flex items-center gap-1 mb-3">
      {Array.from({ length: STEPS }, (_, i) => {
        const n = i + 1;
        let cls = "h-1.5 flex-1 rounded-full ";
        if (n < done) cls += "bg-green-500";
        else if (n === done && done > 0) cls += "bg-primary-500 ring-1 ring-primary-400/40";
        else cls += "bg-slate-700";
        return <div key={i} className={cls} />;
      })}
    </div>
  );
}

// ─── Version card ─────────────────────────────────────────────────────────────

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

function VersionCard({ version, onOpen }: { version: Version; onOpen: () => void }) {
  const dateStr = new Date(version.created_at).toLocaleDateString("sk-SK", {
    day: "numeric", month: "numeric", year: "numeric",
  });

  return (
    <div
      className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden mb-3 cursor-pointer hover:border-slate-700 transition-colors"
      onClick={onOpen}
    >
      <div className="px-5 py-3 border-b border-slate-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="font-mono font-bold text-primary-400 text-sm">{version.version_number}</span>
          {version.name && (
            <span className="text-slate-300 text-sm font-medium">{version.name}</span>
          )}
          <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${versionStatusCls(version.status)}`}>
            {versionStatusLabel(version.status)}
          </span>
        </div>
        <div className="flex items-center gap-3 text-xs text-slate-400">
          <span>Vytvorené {dateStr}</span>
          <svg className="w-4 h-4 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </div>
      </div>
      <div className="px-5 py-4">
        <PipelineBar version={version} />
        <div className="flex items-center justify-between text-xs text-slate-500">
          <span>{version.bug_count} bugov · {version.epics_done}/{version.epic_count} epikov hotových</span>
          <span className="text-primary-400 font-medium">Pokračovať →</span>
        </div>
      </div>
    </div>
  );
}

// ─── ProjectDetailPage ────────────────────────────────────────────────────────

export default function ProjectDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectRead | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    listProjectsApi({ limit: 100 })
      .then((res) => {
        if (cancelled) return;
        const found = res.items.find((p) => p.slug === slug);
        if (!found) { setError("Projekt nebol nájdený."); setLoading(false); return; }
        setProject(found);
        return listVersions(found.id).then((vs) => {
          if (!cancelled) setVersions(vs);
        });
      })
      .catch(() => { if (!cancelled) setError("Nepodarilo sa načítať projekt."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [slug]);

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

  if (error || !project) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <div className="rounded-lg bg-red-500/10 border border-red-500/30 p-4 text-sm text-red-400">
          {error || "Projekt nebol nájdený."}
        </div>
      </div>
    );
  }

  const isMulti = project.category === "multimodule";

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={() => navigate("/projects")}
          className="text-slate-500 hover:text-slate-300 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div className="flex items-center gap-2">
          <h1 className="text-lg font-bold text-slate-100">{project.name}</h1>
          {isMulti && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/20 border border-indigo-500/30 text-indigo-400 font-medium">
              Multi-Module
            </span>
          )}
          <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
            project.status === "active"
              ? "bg-green-500/15 border border-green-500/25 text-green-400"
              : project.status === "paused"
              ? "bg-amber-500/15 border border-amber-500/30 text-amber-400"
              : "bg-slate-700 text-slate-500"
          }`}>
            {project.status}
          </span>
        </div>
      </div>

      {/* Info card */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-5 mb-6">
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-slate-500 text-xs">Slug</span>
            <div className="font-mono text-slate-200 mt-0.5">{project.slug}</div>
          </div>
          {project.repo_url && (
            <div>
              <span className="text-slate-500 text-xs">Repository</span>
              <div className="font-mono text-slate-200 mt-0.5">{project.repo_url}</div>
            </div>
          )}
          {project.description && (
            <div className="col-span-2">
              <span className="text-slate-500 text-xs">Description</span>
              <div className="text-slate-300 mt-0.5">{project.description}</div>
            </div>
          )}
          {(project.backend_port || project.frontend_port || project.db_port || project.ui_design_port) && (
            <div className="col-span-2">
              <span className="text-slate-500 text-xs">Ports</span>
              <div className="flex gap-3 mt-1">
                {project.backend_port && (
                  <span className="text-[11px] font-mono bg-slate-800 border border-slate-700 text-slate-300 px-2 py-0.5 rounded">
                    BE :{project.backend_port}
                  </span>
                )}
                {project.frontend_port && (
                  <span className="text-[11px] font-mono bg-slate-800 border border-slate-700 text-slate-300 px-2 py-0.5 rounded">
                    FE :{project.frontend_port}
                  </span>
                )}
                {project.db_port && (
                  <span className="text-[11px] font-mono bg-slate-800 border border-slate-700 text-slate-300 px-2 py-0.5 rounded">
                    DB :{project.db_port}
                  </span>
                )}
                {project.ui_design_port && (
                  <span className="text-[11px] font-mono bg-slate-800 border border-slate-700 text-slate-300 px-2 py-0.5 rounded">
                    UI :{project.ui_design_port}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Multi-Module shortcut */}
      {isMulti && (
        <div className="rounded-xl border border-indigo-500/30 bg-indigo-500/5 p-4 mb-6 flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold text-slate-100 mb-0.5">Multi-Module projekt</div>
            <div className="text-xs text-slate-500">Spravuj moduly, závislosti a pipeline pre každý modul.</div>
          </div>
          <button
            onClick={() => navigate(`/projects/${slug}/mm`)}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors shrink-0"
          >
            Modul prehľad →
          </button>
        </div>
      )}

      {/* Versions */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest">Verzie</h2>
          <button
            onClick={() => navigate(`/projects/${slug}/versions/new`)}
            className="flex items-center gap-1.5 bg-primary-600 hover:bg-primary-500 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Nová verzia
          </button>
        </div>

        {versions.length === 0 ? (
          <>
            <div className="rounded-xl border border-dashed border-slate-800 p-8 text-center mb-3">
              <p className="text-sm text-slate-500">Žiadne verzie</p>
              <p className="text-xs text-slate-700 mt-1">Vytvor prvú verziu a začni 7-krokový pipeline.</p>
            </div>
            <button
              onClick={() => navigate(`/projects/${slug}/versions/new`)}
              className="w-full rounded-xl border border-dashed border-slate-700 p-4 flex items-center gap-3 text-slate-500 text-sm cursor-pointer hover:border-slate-600 transition-colors"
            >
              <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              Pridať verziu v0.1
            </button>
          </>
        ) : (
          <>
            {versions.map((v) => (
              <VersionCard
                key={v.id}
                version={v}
                onOpen={() => navigate(`/projects/${slug}/versions/${v.id}`)}
              />
            ))}
            {/* Hint for next version */}
            {(() => {
              const last = versions[0];
              const match = last?.version_number.match(/^v?(\d+)\.(\d+)$/);
              const nextLabel = (match && match[1] && match[2])
                ? `v${match[1]}.${parseInt(match[2]) + 1}`
                : "ďalšiu verziu";
              return (
                <button
                  onClick={() => navigate(`/projects/${slug}/versions/new`)}
                  className="w-full rounded-xl border border-dashed border-slate-700 p-4 flex items-center gap-3 text-slate-500 text-sm cursor-pointer hover:border-slate-600 transition-colors"
                >
                  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  Pridať verziu {nextLabel}
                </button>
              );
            })()}
          </>
        )}
      </div>
    </div>
  );
}
