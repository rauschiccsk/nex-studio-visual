import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { listProjectsApi } from "@/services/api/projects";
import { listVersions } from "@/services/api/versions";
import type { ProjectRead } from "@/types";
import type { Version } from "@/types/version";

interface JustCreatedState {
  justCreated?: boolean;
  repoUrl?: string | null;
  backendPort?: number | null;
  frontendPort?: number | null;
  dbPort?: number | null;
}

// ─── Pipeline bar ─────────────────────────────────────────────────────────────

const STEPS = 7;

export function PipelineBar({ version }: { version: Version }) {
  const released = version.status === "released";
  // released = shipped = complete → every segment green.
  // otherwise → green segments proportional to epics-done ratio.
  const ratio = version.epic_count === 0 ? 0 : version.epics_done / version.epic_count;
  const filled = released ? STEPS : Math.round(ratio * STEPS);
  // The purple in-progress highlight sits on the single segment right after
  // the filled ones — but only for an active version that has epics and is
  // not yet full. planned / released / 0-epic never show it.
  const inProgressIdx =
    version.status === "active" && version.epic_count > 0 && filled < STEPS ? filled : -1;

  return (
    <div className="flex items-center gap-1 mb-3">
      {Array.from({ length: STEPS }, (_, i) => {
        let cls = "h-1.5 flex-1 rounded-full ";
        if (i < filled) cls += "bg-[var(--color-status-success)]";
        else if (i === inProgressIdx) cls += "bg-primary-500 ring-1 ring-primary-400/40";
        else cls += "bg-[var(--color-surface-active)]";
        return <div key={i} className={cls} />;
      })}
    </div>
  );
}

// ─── Version card ─────────────────────────────────────────────────────────────

function versionStatusCls(status: string) {
  if (status === "active") return "bg-[var(--color-state-warning-bg)] border border-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]";
  if (status === "released") return "bg-[var(--color-state-success-bg)] border border-[var(--color-state-success-bg)] text-[var(--color-state-success-fg)]";
  return "bg-[var(--color-surface-hover)] border border-[var(--color-border-strong)] text-[var(--color-text-secondary)]";
}

function versionStatusLabel(status: string) {
  if (status === "active") return "Prebieha";
  if (status === "released") return "Vydané";
  return "Plánované";
}

function VersionCard({ version, onOpen }: { version: Version; onOpen: () => void }) {
  const dateStr = new Date(version.created_at).toLocaleDateString("sk-SK", {
    day: "numeric", month: "numeric", year: "numeric",
  });

  return (
    <div
      className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-canvas)] overflow-hidden mb-3 cursor-pointer hover:border-[var(--color-border-default)] transition-colors"
      onClick={onOpen}
    >
      <div className="px-5 py-3 border-b border-[var(--color-border-default)] flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="font-mono font-bold text-primary-400 text-sm">{version.version_number}</span>
          {version.name && (
            <span className="text-[var(--color-text-secondary)] text-sm font-medium">{version.name}</span>
          )}
          <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${versionStatusCls(version.status)}`}>
            {versionStatusLabel(version.status)}
          </span>
        </div>
        <div className="flex items-center gap-3 text-xs text-[var(--color-text-secondary)]">
          <span>Vytvorené {dateStr}</span>
          <svg className="w-4 h-4 text-[var(--color-text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </div>
      </div>
      <div className="px-5 py-4">
        <PipelineBar version={version} />
        <div className="flex items-center justify-between text-xs text-[var(--color-text-muted)]">
          <span>{version.bug_count} bugov · {version.epics_done}/{version.epic_count} epikov hotových</span>
          <span className="text-primary-400 font-medium">Pokračovať →</span>
        </div>
      </div>
    </div>
  );
}

// ─── ProjectDetailPage ────────────────────────────────────────────────────────

export default function ProjectDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const [project, setProject] = useState<ProjectRead | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // "Project just created" banner — shown only immediately after a
  // successful POST /projects navigate. The state is cleared (via
  // history replaceState) the moment this component reads it so a
  // page refresh never re-surfaces the banner.
  const [justCreated, setJustCreated] = useState<JustCreatedState | null>(null);
  useEffect(() => {
    const st = (location.state ?? null) as JustCreatedState | null;
    if (st?.justCreated) {
      setJustCreated(st);
      // Wipe location.state so refresh / back navigation does not re-show.
      window.history.replaceState({}, "");
      // Auto-dismiss after 8 seconds.
      const timer = setTimeout(() => setJustCreated(null), 8000);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [location.state]);

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    listProjectsApi({ limit: 100 })
      .then((res) => {
        if (cancelled) return;
        const found = res.items.find((p) => p.slug === slug);
        if (!found) { setError("Projekt nebol nájdený."); setLoading(false); return; }
        setProject(found);
        return listVersions(found.id).then((vs) => {
          if (!cancelled) setVersions(vs);
        });
      })
      .catch(() => { if (!cancelled) setError("Nepodarilo sa načítať projekt."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [slug]);

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

  if (error || !project) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <div className="rounded-lg bg-[var(--color-state-error-bg)] border border-[var(--color-state-error-bg)] p-4 text-sm text-[var(--color-state-error-fg)]">
          {error || "Projekt nebol nájdený."}
        </div>
      </div>
    );
  }

  const isMulti = project.category === "multimodule";

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* "Just created" banner — visible for 8s right after POST /projects. */}
      {justCreated && (
        <div className="mb-4 rounded-lg border border-[var(--color-state-success-bg)] bg-[var(--color-state-success-bg)] px-4 py-3 flex items-start gap-3">
          <svg
            className="w-5 h-5 text-[var(--color-status-success)] mt-0.5 flex-shrink-0"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
          <div className="flex-1 text-sm text-[var(--color-state-success-fg)]">
            <div className="font-medium">Projekt vytvorený.</div>
            <div className="text-[12px] text-[var(--color-state-success-fg)] mt-0.5 flex flex-wrap gap-x-3">
              {justCreated.repoUrl && (
                <span>
                  GitHub repo:{" "}
                  <a
                    href={`https://github.com/${justCreated.repoUrl}`}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono underline hover:text-[var(--color-state-success-fg)]"
                  >
                    {justCreated.repoUrl}
                  </a>
                </span>
              )}
              {(justCreated.backendPort || justCreated.frontendPort || justCreated.dbPort) && (
                <span className="font-mono">
                  ports {justCreated.backendPort ?? "—"}/{justCreated.frontendPort ?? "—"}/
                  {justCreated.dbPort ?? "—"}
                </span>
              )}
            </div>
          </div>
          <button
            onClick={() => setJustCreated(null)}
            className="text-[var(--color-status-success)] hover:text-[var(--color-state-success-fg)] transition-colors"
            aria-label="Zavrieť"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={() => navigate("/projects")}
          className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div className="flex items-center gap-2">
          <h1 className="text-lg font-bold text-[var(--color-text-primary)]">{project.name}</h1>
          {isMulti && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/20 border border-indigo-500/30 text-[var(--color-accent-primary)] font-medium">
              Multi-Module
            </span>
          )}
          <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
            project.status === "active"
              ? "bg-[var(--color-state-success-bg)] border border-[var(--color-state-success-bg)] text-[var(--color-state-success-fg)]"
              : project.status === "paused"
              ? "bg-[var(--color-state-warning-bg)] border border-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]"
              : "bg-[var(--color-surface-active)] text-[var(--color-text-muted)]"
          }`}>
            {project.status}
          </span>
        </div>
      </div>

      {/* Info card */}
      <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-5 mb-6">
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-[var(--color-text-muted)] text-xs">Slug</span>
            <div className="font-mono text-[var(--color-text-primary)] mt-0.5">{project.slug}</div>
          </div>
          {project.repo_url && (
            <div>
              <span className="text-[var(--color-text-muted)] text-xs">Úložisko</span>
              <div className="font-mono text-[var(--color-text-primary)] mt-0.5">{project.repo_url}</div>
            </div>
          )}
          {project.description && (
            <div className="col-span-2">
              <span className="text-[var(--color-text-muted)] text-xs">Popis</span>
              <div className="text-[var(--color-text-secondary)] mt-0.5">{project.description}</div>
            </div>
          )}
          {(project.backend_port || project.frontend_port || project.db_port) && (
            <div className="col-span-2">
              <span className="text-[var(--color-text-muted)] text-xs">Porty</span>
              <div className="flex gap-3 mt-1">
                {project.backend_port && (
                  <span className="text-[11px] font-mono bg-[var(--color-surface)] border border-[var(--color-border-default)] text-[var(--color-text-secondary)] px-2 py-0.5 rounded">
                    BE :{project.backend_port}
                  </span>
                )}
                {project.frontend_port && (
                  <span className="text-[11px] font-mono bg-[var(--color-surface)] border border-[var(--color-border-default)] text-[var(--color-text-secondary)] px-2 py-0.5 rounded">
                    FE :{project.frontend_port}
                  </span>
                )}
                {project.db_port && (
                  <span className="text-[11px] font-mono bg-[var(--color-surface)] border border-[var(--color-border-default)] text-[var(--color-text-secondary)] px-2 py-0.5 rounded">
                    DB :{project.db_port}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Multi-Module shortcut */}
      {isMulti && (
        <div className="rounded-xl border border-indigo-500/30 bg-indigo-500/5 p-4 mb-6 flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold text-[var(--color-text-primary)] mb-0.5">Multi-Module projekt</div>
            <div className="text-xs text-[var(--color-text-muted)]">Spravuj moduly, závislosti a pipeline pre každý modul.</div>
          </div>
          <button
            onClick={() => navigate(`/projects/${slug}/mm`)}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors shrink-0"
          >
            Modul prehľad →
          </button>
        </div>
      )}

      {/* Versions */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-widest">Verzie</h2>
          <button
            onClick={() => navigate(`/projects/${slug}/versions/new`)}
            className="flex items-center gap-1.5 bg-primary-600 hover:bg-primary-500 text-white text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Nová verzia
          </button>
        </div>

        {versions.length === 0 ? (
          <>
            <div className="rounded-xl border border-dashed border-[var(--color-border-default)] p-8 text-center mb-3">
              <p className="text-sm text-[var(--color-text-muted)]">Žiadne verzie</p>
              <p className="text-xs text-[var(--color-text-muted)] mt-1">Vytvor prvú verziu a začni 7-krokový pipeline.</p>
            </div>
            <button
              onClick={() => navigate(`/projects/${slug}/versions/new`)}
              className="w-full rounded-xl border border-dashed border-[var(--color-border-default)] p-4 flex items-center gap-3 text-[var(--color-text-muted)] text-sm cursor-pointer hover:border-[var(--color-border-strong)] transition-colors"
            >
              <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              Pridať verziu v0.1
            </button>
          </>
        ) : (
          <>
            {versions.map((v) => (
              <VersionCard
                key={v.id}
                version={v}
                onOpen={() => navigate(`/projects/${slug}/versions/${v.id}`)}
              />
            ))}
            {/* Hint for next version */}
            {(() => {
              const last = versions[0];
              const match = last?.version_number.match(/^v?(\d+)\.(\d+)$/);
              const nextLabel = (match && match[1] && match[2])
                ? `v${match[1]}.${parseInt(match[2]) + 1}`
                : "ďalšiu verziu";
              return (
                <button
                  onClick={() => navigate(`/projects/${slug}/versions/new`)}
                  className="w-full rounded-xl border border-dashed border-[var(--color-border-default)] p-4 flex items-center gap-3 text-[var(--color-text-muted)] text-sm cursor-pointer hover:border-[var(--color-border-strong)] transition-colors"
                >
                  <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  Pridať verziu {nextLabel}
                </button>
              );
            })()}
          </>
        )}
      </div>
    </div>
  );
}
