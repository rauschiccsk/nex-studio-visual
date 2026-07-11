/**
 * SpecifikaciaPage — the read-only project-DOCUMENTS surface (spine STEP 2, route /specifikacia).
 *
 * Audit Theme 3 (2026-07-10): previously this rendered exactly ONE hardcoded file
 * (``docs/specs/versions/v<N>/specification.md``) — the manager could not open design.md, the DB-schema doc,
 * their own customer-requirements (Zadanie), etc., so most of what the AI produced was invisible from the
 * cockpit ("DONE = reality the manager can SEE"). Now it lists the pinned version's ``docs/specs/`` documents
 * (via the EXISTING but previously-unwired ``/project-specs/list``), lets the Manažér pick one, and renders it
 * with the SAME ``/project-specs/content`` + SpecMarkdown path. Defaults to the specification (unchanged
 * behaviour when it is the only doc). Read-only; the AI writes + maintains these during the conversation.
 *
 * Honest states: no project pinned → guard; no docs on disk yet → "nothing agreed yet" + a link to the
 * Riadiace centrum; present → the doc picker + the rendered Markdown.
 */

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileText, FolderOpen } from "lucide-react";

import { useActiveContextStore } from "@/store/activeContextStore";
import { getProjectSpecContent, listProjectSpecs, type ProjectSpecDoc } from "@/services/api/projectSpecs";
import { getPipelineBoardApi } from "@/services/api/pipeline";
import { SpecMarkdown } from "@/components/markdown/SpecMarkdown";

// Friendly Slovak names for the known spec documents (fallback = the filename without .md).
const DOC_LABELS: Record<string, string> = {
  "specification.md": "Špecifikácia",
  "design.md": "Návrh",
  "development-spec.md": "Vývojová špecifikácia",
  "customer-requirements.md": "Zadanie zákazníka",
  "DATABASE_SCHEMAS.md": "Databázová schéma",
  "database_schemas.md": "Databázová schéma",
  "RELEASE_NOTES.md": "Poznámky k vydaniu",
};
// The order the pills appear in (most manager-relevant first); anything else sorts alphabetically after.
const DOC_ORDER = [
  "specification.md",
  "design.md",
  "development-spec.md",
  "customer-requirements.md",
  "DATABASE_SCHEMAS.md",
  "database_schemas.md",
  "RELEASE_NOTES.md",
];

function labelFor(filename: string): string {
  return DOC_LABELS[filename] ?? filename.replace(/\.md$/i, "");
}

// The content endpoint's ``path`` is repo-relative (``docs/...``); the list's ``relative_path`` is prefixed
// with ``<slug>/`` — strip it.
function repoPath(doc: ProjectSpecDoc, slug: string): string {
  const prefix = `${slug}/`;
  return doc.relative_path.startsWith(prefix) ? doc.relative_path.slice(prefix.length) : doc.relative_path;
}

// Keep only the pinned version's spec docs: this version's ``docs/specs/versions/v<N>/`` files PLUS the
// project-level ``docs/specs/`` files (e.g. customer-requirements) — but NOT other versions' folders.
function filterVersionDocs(all: ProjectSpecDoc[], slug: string, versionNumber: string): ProjectSpecDoc[] {
  const specsPrefix = `${slug}/docs/specs/`;
  const versionPrefix = `${slug}/docs/specs/versions/v${versionNumber}/`;
  const picked = all.filter(
    (d) =>
      !d.is_directory &&
      /\.md$/i.test(d.filename) &&
      d.relative_path.startsWith(specsPrefix) &&
      (d.relative_path.startsWith(versionPrefix) || !d.category.includes("/versions/")),
  );
  return picked.sort((a, b) => {
    const ia = DOC_ORDER.indexOf(a.filename);
    const ib = DOC_ORDER.indexOf(b.filename);
    const oa = ia === -1 ? 999 : ia;
    const ob = ib === -1 ? 999 : ib;
    return oa - ob || a.filename.localeCompare(b.filename);
  });
}

export default function SpecifikaciaPage() {
  const navigate = useNavigate();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const selectedVersion = useActiveContextStore((s) => s.selectedVersion);

  const slug = selectedProject?.slug;
  const versionNumber = selectedVersion?.versionNumber;
  const versionId = selectedVersion?.versionId;

  const [docs, setDocs] = useState<ProjectSpecDoc[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [activePath, setActivePath] = useState<string | null>(null);

  const [body, setBody] = useState<string | null>(null);
  const [contentLoading, setContentLoading] = useState(false);

  // Durable "Schválená" signal from the board (spine STEP 2) — TRUE once the Špecifikácia was frozen.
  const [specApproved, setSpecApproved] = useState(false);

  // Load the pinned version's document list, filter it, and pick a sensible default (the specification, else
  // the first doc). Re-fetched when the pinned project / version changes.
  useEffect(() => {
    let cancelled = false;
    setDocs([]);
    setActivePath(null);
    setBody(null);
    if (!slug || !versionNumber) return;
    setDocsLoading(true);
    listProjectSpecs()
      .then((res) => {
        if (cancelled) return;
        const filtered = filterVersionDocs(res.documents, slug, versionNumber);
        setDocs(filtered);
        const def = filtered.find((d) => d.filename === "specification.md") ?? filtered[0];
        setActivePath(def ? repoPath(def, slug) : null);
      })
      .catch(() => {
        /* unreachable / none → honest "nothing agreed yet" empty state */
      })
      .finally(() => {
        if (!cancelled) setDocsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, versionNumber]);

  // Load the selected document's content (same endpoint as before), whenever the active pick changes.
  useEffect(() => {
    let cancelled = false;
    setBody(null);
    if (!slug || !activePath) return;
    setContentLoading(true);
    getProjectSpecContent(slug, activePath)
      .then((res) => {
        if (!cancelled && res.is_text) setBody(res.content);
      })
      .catch(() => {
        /* unreadable → fall through to the empty note */
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, activePath]);

  // Durable approval flag from the pipeline board (spec_approved). Keyed on the pinned version.
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

  const hasSpec = useMemo(() => docs.some((d) => d.filename === "specification.md"), [docs]);
  // Badge: approved → "Schválená"; a spec exists but isn't frozen → "Rozpracované"; no spec yet → none.
  const specBadge: "schvalena" | "rozpracovane" | null = specApproved ? "schvalena" : hasSpec ? "rozpracovane" : null;

  if (!selectedProject) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 bg-[var(--color-canvas)] p-6 text-center">
        <FolderOpen className="h-10 w-10 text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Nemáš vybraný projekt</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Dokumenty projektu sú viazané na konkrétny projekt. Otvor <span className="font-mono">Projekty</span> a
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
        <h1 className="text-sm font-semibold text-[var(--color-text-primary)]">Dokumenty</h1>
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

      {/* Document picker — one pill per document the AI produced for this version. Hidden when there is ≤1. */}
      {docs.length > 1 && (
        <div className="flex flex-shrink-0 flex-wrap items-center gap-1.5 border-b border-[var(--color-border-default)] bg-[var(--color-surface)] px-4 py-2">
          {docs.map((d) => {
            const p = repoPath(d, slug!);
            const active = p === activePath;
            return (
              <button
                key={d.relative_path}
                type="button"
                onClick={() => setActivePath(p)}
                title={labelFor(d.filename)}
                className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  active
                    ? "border-primary-500 bg-primary-600 text-white"
                    : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                {labelFor(d.filename)}
              </button>
            );
          })}
        </div>
      )}

      {docsLoading || contentLoading ? (
        <div className="flex flex-1 items-center justify-center p-6 text-center">
          <p className="text-xs text-[var(--color-text-muted)]">Načítavam…</p>
        </div>
      ) : body !== null ? (
        body.trim() ? (
          <div className="flex-1 overflow-y-auto">
            <SpecMarkdown
              body={body}
              className="prose prose-sm dark:prose-invert max-w-none px-6 py-5 text-sm text-[var(--color-text-primary)]"
            />
          </div>
        ) : (
          <div className="flex flex-1 items-center justify-center p-6 text-center">
            <p className="text-xs text-[var(--color-text-muted)]">Tento dokument je zatiaľ prázdny.</p>
          </div>
        )
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
          <p className="max-w-md text-xs text-[var(--color-text-muted)]">
            Zatiaľ tu nie sú žiadne dokumenty. Vznikajú v Riadiacom centre — v rozhovore s AI Agentom sa dohodnete
            na zadaní a AI ich priebežne zapisuje.
          </p>
          <button
            onClick={() => navigate("/riadiace-centrum")}
            className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
          >
            → Otvor Riadiace centrum
          </button>
        </div>
      )}
    </div>
  );
}
