import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Pin, PinOff } from "lucide-react";
import { listProjectsApi } from "@/services/api/projects";
import { listVersions } from "@/services/api/versions";
import { useActiveContextStore } from "@/store/activeContextStore";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";

// ─── Helpers ────────────────────────────────────────────────────────────────

const SLUG_COLORS = [
  "bg-primary-600/20 border-primary-600/30 text-primary-700 dark:text-primary-400",
  "bg-amber-600/20 border-amber-600/30 text-amber-700 dark:text-amber-400",
  "bg-green-600/20 border-green-600/30 text-green-700 dark:text-green-400",
  "bg-purple-600/20 border-purple-600/30 text-purple-700 dark:text-purple-400",
  "bg-rose-600/20 border-rose-600/30 text-rose-700 dark:text-rose-400",
  "bg-cyan-600/20 border-cyan-600/30 text-cyan-700 dark:text-cyan-400",
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
  selected: boolean;
  onOpen: () => void;
  onTogglePin: () => void;
}

function ProjectRow({ project, index, selected, onOpen, onTogglePin }: ProjectRowProps) {
  const color = slugColor(index);
  const initials = slugInitials(project.slug);
  const port = project.backend_port ?? project.frontend_port ?? null;

  const rowClass = selected
    ? "rounded-xl border border-[var(--color-border-default)] border-l-4 border-l-primary-500 bg-primary-500/5 p-4 flex items-center gap-4 transition-colors"
    : "rounded-xl border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-4 flex items-center gap-4 hover:border-[var(--color-border-default)] transition-colors";

  return (
    <div className={rowClass}>
      {/* Slug icon */}
      <div className={`w-10 h-10 rounded-lg border flex items-center justify-center font-bold text-sm shrink-0 ${color}`}>
        {initials}
      </div>

      {/* Name + repo */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="font-semibold text-[var(--color-text-primary)] text-sm">{project.name}</span>
          {selected && (
            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-primary-500/20 border border-primary-500/30 text-primary-700 dark:text-primary-400 font-medium">
              Vybraný
            </span>
          )}
          {project.status === "archived" && (
            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-[var(--color-surface-active)] text-[var(--color-text-muted)]">
              Archivovaný
            </span>
          )}
          {project.status === "paused" && (
            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-[var(--color-state-warning-bg)] border border-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]">
              Pozastavený
            </span>
          )}
        </div>
        <div className="text-xs text-[var(--color-text-muted)] font-mono truncate">
          {project.repo_url || project.slug}
          {port ? ` · :${port}` : ""}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-3 shrink-0">
        <button
          onClick={onTogglePin}
          title={selected ? "Zrušiť výber" : "Označiť ako vybraný"}
          className={
            selected
              ? "text-primary-700 dark:text-primary-400 hover:text-primary-300 transition-colors"
              : "text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors"
          }
        >
          {selected ? <Pin className="w-4 h-4 fill-current" /> : <PinOff className="w-4 h-4" />}
        </button>
        <button
          onClick={onOpen}
          className="text-[11px] text-primary-700 dark:text-primary-400 hover:text-primary-300 transition-colors font-medium"
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
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const setSelectedProject = useActiveContextStore((s) => s.setSelectedProject);
  const setSelectedVersion = useActiveContextStore((s) => s.setSelectedVersion);

  /** Pick a sensible default verzia for the freshly pinned projekt.
   *
   *  Priority:
   *    1. ``status === "active"``  — the verzia currently being worked on
   *    2. first row (list is ``version_number DESC`` — newest first)
   *    3. ``null`` — no versions yet; Workflow stays disabled until one exists
   *
   *  Director directive 2026-05-14: Workflow must be enabled the moment
   *  a project with an existing verzia is pinned — without this auto-pick
   *  the user had to manually open the verzia first which was non-obvious.
   */
  async function pickDefaultVersion(projectId: string): Promise<Version | null> {
    try {
      const versions = await listVersions(projectId);
      if (versions.length === 0) return null;
      const active = versions.find((v) => v.status === "active");
      return active ?? versions[0] ?? null;
    } catch {
      // Network / permission failure — leave version unset so Workflow
      // stays disabled. User can still open the verzia manually via
      // Versions link.
      return null;
    }
  }

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
        <h1 className="text-lg font-bold text-[var(--color-text-primary)]">Projekty</h1>
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
        <div className="flex items-center justify-center py-16 text-[var(--color-text-muted)] text-sm gap-2">
          <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Načítavam…
        </div>
      )}

      {error && !loading && (
        <div className="rounded-lg bg-[var(--color-state-error-bg)] border border-[var(--color-state-error-bg)] p-4 text-sm text-[var(--color-state-error-fg)]">
          {error}
        </div>
      )}

      {!loading && !error && projects.length === 0 && (
        <div className="rounded-xl border border-dashed border-[var(--color-border-default)] p-10 text-center">
          <div className="w-10 h-10 rounded-xl bg-[var(--color-surface)] flex items-center justify-center mx-auto mb-3">
            <svg className="w-5 h-5 text-[var(--color-text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
            </svg>
          </div>
          <p className="text-sm text-[var(--color-text-muted)] mb-1">Žiadne projekty</p>
          <p className="text-xs text-[var(--color-text-muted)]">Vytvor prvý projekt a začni pracovať v NEX Studio.</p>
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
          {projects.map((p, i) => {
            const isSelected = selectedProject?.slug === p.slug;
            return (
              <ProjectRow
                key={p.id}
                project={p}
                index={i}
                selected={isSelected}
                onOpen={() => navigate(`/projects/${p.slug}`)}
                onTogglePin={() => {
                  if (isSelected) {
                    setSelectedProject(null);
                    return;
                  }
                  // Optimistic pin first so the indicator updates
                  // immediately; the version fetch is best-effort.
                  setSelectedProject({ slug: p.slug, name: p.name });
                  void pickDefaultVersion(p.id).then((v) => {
                    if (v) {
                      setSelectedVersion({
                        versionId: v.id,
                        versionNumber: v.version_number,
                      });
                    }
                  });
                }}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
