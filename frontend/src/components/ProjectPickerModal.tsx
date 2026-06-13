/**
 * Modal — choose a project slug from the user's accessible projects.
 *
 * Used by the agent terminal pages (Designer / Implementer / Auditor)
 * before spawning a session, so the new claude CLI process can be
 * anchored to a specific ``/opt/projects/<slug>/`` directory.
 *
 * Lists projects via the existing ``listProjectsApi`` and renders them
 * as a vertical button stack (single-select). The caller receives the
 * chosen ``project.slug`` via ``onPick``.
 */

import { useEffect, useState } from "react";
import { X, FolderOpen, Loader2 } from "lucide-react";
import { Card } from "nex-shared";

import { listProjectsApi } from "@/services/api/projects";
import type { ProjectRead } from "@/types";

export interface ProjectPickerModalProps {
  title: string;
  description?: string;
  onPick: (slug: string) => void;
  onCancel: () => void;
}

export function ProjectPickerModal({
  title,
  description,
  onPick,
  onCancel,
}: ProjectPickerModalProps) {
  const [projects, setProjects] = useState<ProjectRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    setError("");
    listProjectsApi({ limit: 100 })
      .then((res) => setProjects(res.items))
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : "Nepodarilo sa načítať projekty.";
        setError(msg);
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4">
      <Card className="w-full max-w-lg p-5 shadow-2xl">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
            {description && (
              <p className="mt-0.5 text-xs text-slate-500">{description}</p>
            )}
          </div>
          <button
            onClick={onCancel}
            className="text-slate-500 hover:text-slate-300"
            title="Zrušiť"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {error && (
          <div className="mb-3 rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center gap-2 py-6 text-xs text-slate-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Načítavam projekty…
          </div>
        ) : projects.length === 0 ? (
          <div className="py-6 text-center text-xs text-slate-600">
            Žiadne projekty.
          </div>
        ) : (
          <div className="max-h-80 overflow-y-auto">
            <div className="flex flex-col gap-1">
              {projects.map((p) => (
                <button
                  key={p.id}
                  onClick={() => onPick(p.slug)}
                  className="flex items-center gap-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] px-3 py-2 text-left text-sm text-[var(--color-text-primary)] transition-colors hover:border-primary-500 hover:bg-[var(--color-surface-hover)]"
                >
                  <FolderOpen className="h-4 w-4 shrink-0 text-primary-400" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{p.name}</div>
                    <div className="truncate font-mono text-[10px] text-slate-500">{p.slug}</div>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
