/**
 * SpecifikaciaPage — the read-only "Špecifikácia" surface (spine STEP 1, route /specifikacia).
 *
 * Step-1 placeholder: a light read-only shell for the sidebar's project-scoped Špecifikácia item. The real
 * rendered spec (the agreed .md, the output of the Riadiace-centrum conversation) is wired in step 2; for now
 * this is an honest "nothing agreed yet" shell so the nav item + route resolve without a 404.
 */

import { useNavigate } from "react-router-dom";
import { FileText, FolderOpen } from "lucide-react";

import { useActiveContextStore } from "@/store/activeContextStore";

export default function SpecifikaciaPage() {
  const navigate = useNavigate();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);

  if (!selectedProject) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 bg-[var(--color-canvas)] p-6 text-center">
        <FolderOpen className="h-10 w-10 text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Nemáš vybraný projekt</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Špecifikácia je viazaná na konkrétny projekt. Otvor <span className="font-mono">Projekty</span> a
          pripni projekt.
        </p>
        <button
          onClick={() => navigate("/projects")}
          className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
        >
          → Otvor Projekty
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-[var(--color-canvas)]">
      <div className="flex flex-shrink-0 items-center gap-2 border-b border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-2.5">
        <FileText className="h-4 w-4 text-[var(--color-text-muted)]" />
        <h1 className="text-sm font-semibold text-[var(--color-text-primary)]">Špecifikácia</h1>
        <span className="text-[var(--color-text-muted)]">·</span>
        <span className="truncate text-xs text-[var(--color-text-secondary)]">
          {selectedProject.name}
          {selectedVersion && (
            <span className="text-[var(--color-text-muted)]"> · {selectedVersion.versionNumber}</span>
          )}
        </span>
      </div>

      <div className="flex flex-1 items-center justify-center p-6 text-center">
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Špecifikácia sa objaví, keď sa v Riadiacom centre dohodneme na zadaní. Zatiaľ tu nič nie je.
        </p>
      </div>
    </div>
  );
}
