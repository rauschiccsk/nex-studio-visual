import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import type { ProjectRead } from "@/types";

const SLUG_COLORS = [
  "bg-primary-600/20 border-primary-600/30 text-primary-400",
  "bg-amber-600/20 border-amber-600/30 text-amber-400",
  "bg-green-600/20 border-green-600/30 text-green-400",
  "bg-purple-600/20 border-purple-600/30 text-purple-400",
  "bg-rose-600/20 border-rose-600/30 text-rose-400",
  "bg-cyan-600/20 border-cyan-600/30 text-cyan-400",
];

function slugInitials(slug: string): string {
  return slug
    .split("-")
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

interface ProjectCardProps {
  project: ProjectRead;
  index: number;
  onOpen: () => void;
}

function ProjectCard({ project, index, onOpen }: ProjectCardProps) {
  const color = SLUG_COLORS[index % SLUG_COLORS.length];
  const initials = slugInitials(project.slug);
  const isMulti = project.category === "multimodule";

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 hover:border-slate-700 transition-colors">
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-slate-100">{project.name}</span>
            {isMulti && (
              <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-indigo-500/20 border border-indigo-500/30 text-indigo-400 font-medium">
                MM
              </span>
            )}
          </div>
          <div className="text-xs text-slate-500 font-mono mt-0.5">
            {project.repo_url || project.slug}
          </div>
        </div>
        <div className={`w-8 h-8 rounded-lg border flex items-center justify-center font-bold text-xs shrink-0 ${color}`}>
          {initials}
        </div>
      </div>

      {/* Progress bar placeholder */}
      <div className="mb-3">
        <div className="flex justify-between items-center mb-1">
          <span className="text-[10px] text-slate-500">— · —</span>
          <span className="text-[10px] text-slate-500">—%</span>
        </div>
        <div className="h-1.5 bg-slate-800 rounded-full" />
      </div>

      <div className="flex items-center justify-between">
        <div className="flex gap-2 text-[10px] text-slate-500">
          <span>— verzií</span>
        </div>
        <button
          onClick={onOpen}
          className="text-[10px] text-primary-400 hover:text-primary-300 transition-colors"
        >
          → Otvoriť
        </button>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectRead[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    listProjectsApi({ limit: 6, status: "active" })
      .then((res) => { if (!cancelled) setProjects(res.items); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* My Projects */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest">
            Moje projekty
          </h2>
          <button
            onClick={() => navigate("/projects/new")}
            className="flex items-center gap-1.5 text-xs text-primary-400 hover:text-primary-300 font-medium transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Nový projekt
          </button>
        </div>

        {loading ? (
          <div className="flex justify-center py-10 text-slate-600 text-sm">Načítavam…</div>
        ) : projects.length === 0 ? (
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
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {projects.map((p, i) => (
              <ProjectCard
                key={p.id}
                project={p}
                index={i}
                onOpen={() => navigate(`/projects/${p.slug}`)}
              />
            ))}
            {/* Add new card */}
            <button
              onClick={() => navigate("/projects/new")}
              className="rounded-xl border border-dashed border-slate-700 bg-transparent p-4 hover:border-primary-500/50 transition-all flex flex-col items-center justify-center min-h-[140px] gap-2"
            >
              <div className="w-9 h-9 rounded-full border-2 border-dashed border-slate-600 flex items-center justify-center">
                <svg className="w-4 h-4 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
              </div>
              <span className="text-xs text-slate-500">Nový projekt</span>
            </button>
          </div>
        )}
      </div>

      {/* Continue work — placeholder until Phase 3 */}
      {projects.length > 0 && (
        <div>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-3">
            Pokračuj v práci
          </h2>
          <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 text-center text-sm text-slate-600">
            Dostupné po pridaní verzie a spustení pipeline.
          </div>
        </div>
      )}
    </div>
  );
}
