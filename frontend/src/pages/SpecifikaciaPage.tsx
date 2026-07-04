/**
 * SpecifikaciaPage — the read-only "Špecifikácia" surface (spine STEP 2, route /specifikacia).
 *
 * Renders the ONE agreed specification document (``docs/specs/versions/v<N>/specification.md``) read-only.
 * The AI partner writes + maintains it during the Riadiace-centrum conversation and it is frozen on
 * "Schváliť Špecifikáciu"; there is exactly ONE physical file (no second copy → no drift), read in-app via
 * the EXISTING getProjectSpecContent endpoint — the same file PhaseArtifact already reads (source_path ==
 * /opt/projects/<slug>).
 *
 * Three honest states: no project pinned → guard; no spec on disk yet (404 / empty) → "nothing agreed yet"
 * + a link back to the Riadiace centrum where it is written; present → the rendered Markdown. The page is a
 * plain preview both DURING drafting and AFTER approval — it always shows the current single source of truth.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileText, FolderOpen } from "lucide-react";

import { useActiveContextStore } from "@/store/activeContextStore";
import { getProjectSpecContent } from "@/services/api/projectSpecs";
import { getPipelineBoardApi } from "@/services/api/pipeline";
import { SpecMarkdown } from "@/components/markdown/SpecMarkdown";

export default function SpecifikaciaPage() {
  const navigate = useNavigate();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);

  const slug = selectedProject?.slug;
  const versionNumber = selectedVersion?.versionNumber;
  const versionId = selectedVersion?.versionId;

  // Read the ONE on-disk Špecifikácia (docs/specs/versions/v<N>/specification.md) — the same file the
  // Príprava artifact reads (PhaseArtifact.tsx). Re-fetched when the pinned project / version changes.
  const [body, setBody] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Durable "Schválená" signal from the board (spine STEP 2 follow-up) — TRUE once the Špecifikácia was
  // frozen (≥1 kind='approval' message). Read from the board, NOT the truncated recent_messages tail.
  const [specApproved, setSpecApproved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setBody(null);
    if (!slug || !versionNumber) return;
    const path = `docs/specs/versions/v${versionNumber}/specification.md`;
    setLoading(true);
    getProjectSpecContent(slug, path)
      .then((res) => {
        if (!cancelled && res.is_text && res.content.trim()) setBody(res.content);
      })
      .catch(() => {
        /* not written yet / unreadable → fall through to the honest "nothing agreed yet" empty state */
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, versionNumber]);

  // Durable approval flag — fetched from the pipeline board (spec_approved). Absent board / no pipeline yet
  // → stays false (honest "not approved"). Keyed on the pinned version so it refreshes on re-pin.
  useEffect(() => {
    let cancelled = false;
    setSpecApproved(false);
    if (!versionId) return;
    getPipelineBoardApi(versionId)
      .then((board) => {
        if (!cancelled) setSpecApproved(board.spec_approved === true);
      })
      .catch(() => {
        /* no pipeline / unreachable → leave false (never falsely claim "Schválená") */
      });
    return () => {
      cancelled = true;
    };
  }, [versionId]);

  // Three honest badge states: approved → "Schválená"; a spec exists but isn't frozen → "Rozpracované";
  // no spec on disk yet → no badge. spec_approved implies a frozen spec, so it takes precedence.
  const specBadge: "schvalena" | "rozpracovane" | null = specApproved
    ? "schvalena"
    : body
      ? "rozpracovane"
      : null;

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
        {specBadge === "schvalena" && (
          <span className="ml-auto flex-shrink-0 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
            Schválená
          </span>
        )}
        {specBadge === "rozpracovane" && (
          <span className="ml-auto flex-shrink-0 rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-400">
            Rozpracované
          </span>
        )}
      </div>

      {body ? (
        <div className="flex-1 overflow-y-auto">
          <SpecMarkdown
            body={body}
            className="prose prose-sm dark:prose-invert max-w-none px-6 py-5 text-sm text-[var(--color-text-primary)]"
          />
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
          <p className="max-w-md text-xs text-[var(--color-text-muted)]">
            {loading
              ? "Načítavam Špecifikáciu…"
              : "Špecifikácia zatiaľ nie je napísaná. Vzniká v Riadiacom centre — v rozhovore s AI Agentom sa dohodnete na zadaní a AI ju priebežne zapisuje ako jeden dokument."}
          </p>
          {!loading && (
            <button
              onClick={() => navigate("/riadiace-centrum")}
              className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
            >
              → Otvor Riadiace centrum
            </button>
          )}
        </div>
      )}
    </div>
  );
}
