/**
 * ProjectLayout — shared header + tab navigation for all project sub-pages.
 *
 * Route: ``projects/:slug`` (parent / layout route)
 *
 * Fetches the project once by slug, renders the project header and tab bar,
 * then delegates the active tab's content to a child route via
 * ``<Outlet>``.  Child pages receive the resolved project through React
 * Router's outlet-context so they avoid a redundant projects-list fetch.
 *
 * File intentionally kept as ``ProjectPage.tsx`` to minimise git churn;
 * the exported component is named ``ProjectLayout``.
 */

import { useEffect, useState } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";
import {
  AlertCircle,
  Database,
  ExternalLink,
  Globe,
  Layers,
  Package,
  Server,
} from "lucide-react";

import { api, ApiError } from "@/services/api";
import type { ProjectRead } from "@/types";

// ── Outlet context ──────────────────────────────────────────────────────────

/**
 * Passed to every child route via ``<Outlet context={...}>``.
 *
 * Child pages import this type and call
 * ``useOutletContext<ProjectLayoutContext>()`` to receive the resolved project
 * without making their own API call.
 */
export interface ProjectLayoutContext {
  project: ProjectRead;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

const STATUS_BADGE: Record<string, string> = {
  active:   "bg-green-500/15 text-green-400 border border-green-500/30",
  paused:   "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30",
  archived: "bg-gray-500/15 text-gray-400 border border-gray-500/30",
};

const STATUS_LABEL: Record<string, string> = {
  active:   "Aktívny",
  paused:   "Pozastavený",
  archived: "Archivovaný",
};

// ── Component ────────────────────────────────────────────────────────────────

function ProjectLayout() {
  const { slug } = useParams<{ slug: string }>();

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [loading, setLoading]  = useState(true);
  const [error,   setError]    = useState<string | null>(null);

  useEffect(() => {
    api
      .get<{ items: ProjectRead[]; total: number }>("/projects")
      .then((data) => {
        const found = data.items.find((p) => p.slug === slug);
        if (!found) {
          setError(`Projekt '${slug}' nebol nájdený.`);
        } else {
          setProject(found);
        }
      })
      .catch((err) => {
        setError(
          err instanceof ApiError ? err.message : "Nepodarilo sa načítať projekt.",
        );
      })
      .finally(() => setLoading(false));
  }, [slug]);

  // ── Loading ──────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-gray-500 text-sm">
        Načítavam projekt…
      </div>
    );
  }

  // ── Error ────────────────────────────────────────────────────────────────

  if (error || !project) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
        <AlertCircle className="h-4 w-4 shrink-0" />
        {error ?? "Neznáma chyba."}
      </div>
    );
  }

  const base = `/projects/${project.slug}`;

  const tabs = [
    { label: "Prehľad",   to: base,              end: true  },
    { label: "Verzie",    to: `${base}/versions`, end: false },
    { label: "Moduly",    to: `${base}/modules`,  end: false },
    { label: "Delegácia", to: `${base}/delegate`, end: false },
    { label: "KB",        to: `${base}/kb`,       end: false },
    { label: "Správy",    to: `${base}/reports`,  end: false },
  ];

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <section className="space-y-6">
      {/* ── Project header ── */}
      <div className="rounded-xl border border-gray-700 bg-gray-800 p-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-gray-500">
              {project.category === "multimodule"
                ? <Layers className="h-5 w-5" />
                : <Package className="h-5 w-5" />}
            </span>
            <div className="min-w-0">
              <h2 className="text-xl font-bold text-gray-100 truncate">
                {project.name}
              </h2>
              <p className="mt-0.5 font-mono text-xs text-gray-500">
                {project.slug}
              </p>
            </div>
          </div>

          <span
            className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_BADGE[project.status] ?? STATUS_BADGE["archived"]}`}
          >
            {STATUS_LABEL[project.status] ?? project.status}
          </span>
        </div>

        {project.description && (
          <p className="mt-3 text-sm text-gray-400">{project.description}</p>
        )}

        {/* Port + repo badges */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {project.backend_port && (
            <span className="flex items-center gap-1.5 rounded-md bg-gray-700/60 px-2.5 py-1 text-xs text-gray-300">
              <Server className="h-3.5 w-3.5 text-gray-500" />
              BE&nbsp;{project.backend_port}
            </span>
          )}
          {project.frontend_port && (
            <span className="flex items-center gap-1.5 rounded-md bg-gray-700/60 px-2.5 py-1 text-xs text-gray-300">
              <Globe className="h-3.5 w-3.5 text-gray-500" />
              FE&nbsp;{project.frontend_port}
            </span>
          )}
          {project.db_port && (
            <span className="flex items-center gap-1.5 rounded-md bg-gray-700/60 px-2.5 py-1 text-xs text-gray-300">
              <Database className="h-3.5 w-3.5 text-gray-500" />
              DB&nbsp;{project.db_port}
            </span>
          )}
          {project.repo_url && (
            <a
              href={`https://github.com/${project.repo_url}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 rounded-md bg-gray-700/60 px-2.5 py-1 text-xs text-gray-300 hover:bg-gray-700 hover:text-white transition-colors"
            >
              <ExternalLink className="h-3.5 w-3.5 text-gray-500" />
              {project.repo_url}
            </a>
          )}
        </div>
      </div>

      {/* ── Tab navigation ── */}
      <div className="border-b border-gray-700 -mt-2 flex overflow-x-auto">
        {tabs.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) =>
              [
                "px-4 py-2.5 text-sm font-medium border-b-2 transition-colors focus:outline-none whitespace-nowrap",
                isActive
                  ? "border-primary text-primary"
                  : "border-transparent text-gray-400 hover:text-gray-200 hover:border-gray-600",
              ].join(" ")
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </div>

      {/* ── Child route content ── */}
      <Outlet context={{ project } satisfies ProjectLayoutContext} />
    </section>
  );
}

export default ProjectLayout;
