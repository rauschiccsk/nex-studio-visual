import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import type { ProjectRead } from "@/types";

// ─── Helpers ────────────────────────────────────────────────────────────────

const SLUG_COLORS = [
  "bg-primary-600/20 border-primary-600/30 text-primary-400",
  "bg-amber-600/20 border-amber-600/30 text-amber-400",
  "bg-green-600/20 border-green-600/30 text-green-400",
  "bg-purple-600/20 border-purple-600/30 text-purple-400",
  "bg-rose-600/20 border-rose-600/30 text-rose-400",
  "bg-cyan-600/20 border-cyan-600/30 text-cyan-400",
];

function slugColor(index: number) {
  return SLUG_COLORS[index % SLUG_COLORS.length];
}

function slugInitials(slug: string): string {
  return slug
    .split("-")
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

// ─── ProjectRow ──────────────────────────────────────────────────────────────

interface ProjectRowProps {
  project: ProjectRead;
  index: number;
  onOpen: () => void;
}

function ProjectRow({ project, index, onOpen }: ProjectRowProps) {
  const color = slugColor(index);
  const initials = slugInitials(project.slug);
  const isMulti = project.category === "multimodule";
  const port = project.backend_port ?? project.frontend_port ?? null;

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 flex items-center gap-4 hover:border-slate-700 transition-colors">
      {/* Slug icon */}
      <div className={`w-10 h-10 rounded-lg border flex items-center justify-center font-bold text-sm shrink-0 ${color}`}>
        {initials}
      </div>

      {/* Name + repo */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="font-semibold text-slate-100 text-sm">{project.name}</span>
          {isMulti && (
            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-indigo-500/20 border border-indigo-500/30 text-indigo-400 font-medium">
              MM
            </span>
          )}
          {project.status === "archived" && (
            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-slate-700 text-slate-500">
              Archived
            </span>
          )}
          {project.status === "paused" && (
            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-amber-500/20 border border-amber-500/30 text-amber-400">
              Paused
            </span>
          )}
        </div>
        <div className="text-xs text-slate-500 font-mono truncate">
          {project.repo_url || project.slug}
          {port ? ` · :${port}` : ""}
        </div>
      </div>

      {/* Versions — placeholder until Phase 3 */}
      <div className="text-center shrink-0 w-12">
        <div className="text-sm font-semibold text-slate-200">—</div>
        <div className="text-[10px] text-slate-500">verzií</div>
      </div>

      {/* Active version — placeholder until Phase 3 */}
      <div className="text-center shrink-0 w-14">
        <div className="text-sm font-semibold text-primary-400">—</div>
        <div className="text-[10px] text-slate-500">verzia</div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 shrink-0">
        <button
          onClick={onOpen}
          className="text-[11px] text-primary-400 hover:text-primary-300 transition-colors font-medium"
        >
          → Otvoriť
        </button>
      </div>
    </div>
  );
}

// ─── ProjectsPage ────────────────────────────────────────────────────────────

export default function ProjectsPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listProjectsApi({ limit: 100 })
      .then((res) => { if (!cancelled) setProjects(res.items); })
      .catch(() => { if (!cancelled) setError("Nepodarilo sa načítať projekty."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-bold text-slate-100">Projekty</h1>
        <button
          onClick={() => navigate("/projects/new")}
          className="flex items-center gap-1.5 bg-primary-600 hover:bg-primary-500 text-white text-sm font-medium px-3 py-1.5 rounded-lg transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          Nový projekt
        </button>
      </div>

      {/* States */}
      {loading && (
        <div className="flex items-center justify-center py-16 text-slate-500 text-sm gap-2">
          <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Načítavam…
        </div>
      )}

      {error && !loading && (
        <div className="rounded-lg bg-red-500/10 border border-red-500/30 p-4 text-sm text-red-400">
          {error}
        </div>
      )}

      {!loading && !error && projects.length === 0 && (
        <div className="rounded-xl border border-dashed border-slate-800 p-10 text-center">
          <div className="w-10 h-10 rounded-xl bg-slate-800 flex items-center justify-center mx-auto mb-3">
            <svg className="w-5 h-5 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
            </svg>
          </div>
          <p className="text-sm text-slate-500 mb-1">Žiadne projekty</p>
          <p className="text-xs text-slate-700">Vytvor prvý projekt a začni s NEX Studio pipeline.</p>
          <button
            onClick={() => navigate("/projects/new")}
            className="mt-4 inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Nový projekt
          </button>
        </div>
      )}

      {!loading && !error && projects.length > 0 && (
        <div className="space-y-3">
          {projects.map((p, i) => (
            <ProjectRow
              key={p.id}
              project={p}
              index={i}
              onOpen={() => navigate(`/projects/${p.slug}`)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
