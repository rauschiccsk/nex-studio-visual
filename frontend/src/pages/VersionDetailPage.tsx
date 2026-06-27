import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { getVersion } from "@/services/api/versions";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";
import { useActiveContextSync } from "@/hooks/useActiveContextSync";

// ─── Status helpers ───────────────────────────────────────────────────────────

function versionStatusCls(status: string) {
  if (status === "active") return "bg-[var(--color-state-warning-bg)] border border-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]";
  if (status === "released") return "bg-[var(--color-state-success-bg)] border border-[var(--color-state-success-bg)] text-[var(--color-state-success-fg)]";
  return "bg-[var(--color-surface-active)] border border-[var(--color-border-strong)] text-[var(--color-text-secondary)]";
}

function versionStatusLabel(status: string) {
  if (status === "active") return "Prebieha";
  if (status === "released") return "Vydané";
  return "Plánované";
}

// ─── VersionDetailPage ────────────────────────────────────────────────────────

export default function VersionDetailPage() {
  const { slug, versionId } = useParams<{ slug: string; versionId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<ProjectRead | null>(null);
  const [version, setVersion] = useState<Version | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useActiveContextSync(project, version);

  useEffect(() => {
    if (!slug || !versionId) return;
    let cancelled = false;

    Promise.all([
      listProjectsApi({ limit: 100 }).then((res) => res.items.find((p) => p.slug === slug) ?? null),
      getVersion(versionId),
    ])
      .then(([proj, ver]) => {
        if (cancelled) return;
        if (!proj) { setError("Projekt nebol nájdený."); return; }
        setProject(proj);
        setVersion(ver);
      })
      .catch(() => { if (!cancelled) setError("Nepodarilo sa načítať dáta."); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [slug, versionId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-[var(--color-text-muted)] text-sm gap-2">
        <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        Načítavam…
      </div>
    );
  }

  if (error || !project || !version) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <div className="rounded-lg bg-[var(--color-state-error-bg)] border border-[var(--color-state-error-bg)] p-4 text-sm text-[var(--color-state-error-fg)]">
          {error || "Verzia nebola nájdená."}
        </div>
      </div>
    );
  }

  const epicCount = version.epic_count ?? 0;
  const epicsDone = version.epics_done ?? 0;

  return (
    <div className="flex flex-col h-full">
      {/* ── Header ── */}
      <div className="border-b border-[var(--color-border-default)] bg-[var(--color-surface-hover)] shrink-0">
        <div className="flex items-center gap-3 px-5 py-2.5">
          <button
            onClick={() => navigate(`/projects/${slug}`)}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-[var(--color-text-secondary)] font-medium">{project.name}</span>
            <span className="text-[var(--color-text-muted)]">·</span>
            <span className="bg-[var(--color-surface)] text-[var(--color-text-secondary)] font-mono px-2 py-0.5 rounded">
              {version.version_number}
            </span>
            {version.name && (
              <span className="text-[var(--color-text-secondary)]">{version.name}</span>
            )}
          </div>
          <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${versionStatusCls(version.status)}`}>
            {versionStatusLabel(version.status)}
          </span>
          <div className="flex-1" />
          {/* Stats */}
          <div className="flex items-center gap-5 text-center">
            <div>
              <div className="text-sm font-bold text-[var(--color-text-primary)]">{epicsDone}/{epicCount}</div>
              <div className="text-[10px] text-[var(--color-text-secondary)]">epic hotových</div>
            </div>
            <div>
              <div className="text-sm font-bold text-[var(--color-text-primary)]">{version.epic_count}</div>
              <div className="text-[10px] text-[var(--color-text-secondary)]">epic</div>
            </div>
            <div>
              <div className={`text-sm font-bold ${version.bug_count > 0 ? "text-[var(--color-status-error)]" : "text-[var(--color-text-primary)]"}`}>
                {version.bug_count}
              </div>
              <div className="text-[10px] text-[var(--color-text-secondary)]">chyby</div>
            </div>
          </div>
        </div>
      </div>

      {/* ── Body ── */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-3xl mx-auto">
          <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-5">
            <div className="text-sm font-semibold text-[var(--color-text-primary)] mb-1">
              {version.version_number}{version.name ? ` — ${version.name}` : ""}
            </div>
            <div className="text-xs text-[var(--color-text-muted)]">
              {epicCount} epic · {epicsDone} hotových · {version.bug_count} chýb.
              Špecifikáciu, návrh a kód tejto verzie pripravuje AI Agent; výsledok overuje Auditor.
              Fázy (Príprava → Návrh → Programovanie → Verifikácia) sleduješ vo Vývoji.
            </div>
            <button
              type="button"
              onClick={() => navigate("/vyvoj")}
              className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              Otvoriť vo Vývoji
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
