/**
 * ProjectOverview — "Prehľad" tab content.
 *
 * Route: ``projects/:slug`` (index child of ProjectLayout)
 *
 * Receives the already-resolved project from the parent ``ProjectLayout``
 * via React Router's outlet context — no additional API call needed.
 */

import { Calendar, Layers, Package, Shield } from "lucide-react";
import { useOutletContext } from "react-router-dom";

import type { ProjectLayoutContext } from "./ProjectPage";

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

function InfoRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-2 py-2 border-b border-gray-700/60 last:border-0">
      <span className="w-32 shrink-0 text-xs text-gray-500">{label}</span>
      <span className="text-sm text-gray-300">{children}</span>
    </div>
  );
}

// ── Component ────────────────────────────────────────────────────────────────

function ProjectOverview() {
  const { project } = useOutletContext<ProjectLayoutContext>();

  const createdAt = new Date(project.created_at).toLocaleDateString("sk-SK", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  return (
    <div className="rounded-xl border border-gray-700 bg-gray-800 p-6">
      <h3 className="mb-4 text-sm font-semibold text-gray-400 uppercase tracking-wide">
        Detaily projektu
      </h3>

      <div className="divide-y divide-gray-700/60">
        <InfoRow label="Kategória">
          <span className="flex items-center gap-1.5">
            {project.category === "multimodule" ? (
              <>
                <Layers className="h-3.5 w-3.5" />
                Multimodule
              </>
            ) : (
              <>
                <Package className="h-3.5 w-3.5" />
                Single module
              </>
            )}
          </span>
        </InfoRow>

        <InfoRow label="Status">
          <span
            className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[project.status]}`}
          >
            {STATUS_LABEL[project.status]}
          </span>
        </InfoRow>

        {project.source_path && (
          <InfoRow label="Zdrojový kód">
            <span className="font-mono text-xs">{project.source_path}</span>
          </InfoRow>
        )}

        {project.kb_path && (
          <InfoRow label="Knowledge base">
            <span className="font-mono text-xs">{project.kb_path}</span>
          </InfoRow>
        )}

        <InfoRow label="Guardian">
          <span className="flex items-center gap-1.5">
            <Shield
              className={`h-3.5 w-3.5 ${project.guardian_enabled ? "text-green-400" : "text-gray-600"}`}
            />
            {project.guardian_enabled ? "Zapnutý" : "Vypnutý"}
          </span>
        </InfoRow>

        <InfoRow label="Vytvorený">
          <span className="flex items-center gap-1.5">
            <Calendar className="h-3.5 w-3.5 text-gray-500" />
            {createdAt}
          </span>
        </InfoRow>
      </div>
    </div>
  );
}

export default ProjectOverview;
