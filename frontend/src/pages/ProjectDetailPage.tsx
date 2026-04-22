import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import type { ProjectRead } from "@/types";

export default function ProjectDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!slug) return;
    listProjectsApi({ limit: 100 })
      .then((res) => {
        const found = res.items.find((p) => p.slug === slug);
        if (found) setProject(found);
        else setError("Projekt nebol nájdený.");
      })
      .catch(() => setError("Nepodarilo sa načítať projekt."))
      .finally(() => setLoading(false));
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
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-5 mb-4">
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
          {(project.backend_port || project.frontend_port || project.db_port) && (
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
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Versions placeholder */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest">Verzie</h2>
          <button className="flex items-center gap-1.5 text-xs text-primary-400 hover:text-primary-300 font-medium transition-colors">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Nová verzia
          </button>
        </div>
        <div className="rounded-xl border border-dashed border-slate-800 p-8 text-center">
          <p className="text-sm text-slate-500">Žiadne verzie</p>
          <p className="text-xs text-slate-700 mt-1">Vytvor prvú verziu a začni 7-krokový pipeline.</p>
        </div>
      </div>
    </div>
  );
}
