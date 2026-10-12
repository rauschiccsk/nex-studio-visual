import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Ban,
  CheckCircle2,
  CircleAlert,
  Clock,
  ExternalLink,
  FolderOpen,
  GitBranch,
  HelpCircle,
  Loader2,
  RefreshCw,
  Rocket,
} from "lucide-react";

import { fmtDeployTime } from "@/components/deploy/version";
import { choices, duration, filterRuns, NO_FILTER, shownCount, type RunFilter } from "@/lib/ciRuns";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import { getCiRuns, type CiRun, type CiRuns } from "@/services/api/ciRuns";
import { useActiveContextStore } from "@/store/activeContextStore";

/**
 * DEV-84 — the repository tab: the selected project's CI runs as GitHub's Actions page lists them, so the manager does not keep
 * GitHub open on another monitor. Adds what only the cockpit knows: the task a commit belongs to (a link into the
 * task plan) and the customers whose UAT or PROD runs the commit. Read-only; the server asks GitHub for everybody.
 */

const STATES: { value: string; label: string }[] = [
  { value: "success", label: "Prešlo" },
  { value: "failure", label: "Zlyhalo" },
  { value: "running", label: "Beží" },
  { value: "waiting", label: "Čaká" },
  { value: "cancelled", label: "Zrušené" },
  { value: "other", label: "Bez výsledku" },
];

interface Look {
  icon: typeof CheckCircle2;
  tone: string;
  spin?: boolean;
}

const UNKNOWN_LOOK: Look = { icon: HelpCircle, tone: "text-[var(--color-text-muted)]" };

const STATE_LOOK: Record<string, Look> = {
  success: { icon: CheckCircle2, tone: "text-[var(--color-state-success-fg)]" },
  failure: { icon: CircleAlert, tone: "text-[var(--color-state-error-fg)]" },
  running: { icon: Loader2, tone: "text-[var(--color-state-warning-fg)]", spin: true },
  waiting: { icon: Clock, tone: "text-[var(--color-state-warning-fg)]" },
  cancelled: { icon: Ban, tone: "text-[var(--color-text-muted)]" },
  other: UNKNOWN_LOOK,
};

const FIELD =
  "rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-xs text-[var(--color-text-primary)] pointer-coarse:min-h-11";

export default function RepozitarPage() {
  const navigate = useNavigate();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const setSelectedProject = useActiveContextStore((s) => s.setSelectedProject);
  const setSelectedVersion = useActiveContextStore((s) => s.setSelectedVersion);
  // DEV-85 — a link from Telegram names the project (`?projekt=nex-inbox`): it opens even when another one is selected.
  const [params, setParams] = useSearchParams();
  const linked = params.get("projekt");
  const slug = linked ?? selectedProject?.slug;
  const [data, setData] = useState<CiRuns | null>(null);
  const [error, setError] = useState<HumanError | null>(null);
  const [filter, setFilter] = useState<RunFilter>(NO_FILTER);

  const load = useCallback(() => {
    if (!slug) return Promise.resolve();
    // `Promise.resolve().then` — a missing function throws synchronously and would take the whole screen down.
    return Promise.resolve()
      .then(() => getCiRuns(slug))
      .then((d) => {
        setData(d);
        setError(null);
        if (linked) {
          // The link's project becomes the selected one — the other tabs follow it — and the address is plain again.
          setSelectedProject({ slug: linked, name: d.project_name });
          setParams({}, { replace: true });
        }
      })
      .catch((e: unknown) => setError(humanizeApiError(e, "Načítanie behov zlyhalo")));
  }, [slug, linked, setSelectedProject, setParams]);

  const refreshMs = (data?.refresh_seconds ?? 30) * 1000;
  useEffect(() => {
    setData(null);
    setFilter(NO_FILTER);
  }, [slug]);
  useEffect(() => {
    if (!slug) return;
    void load();
    const timer = setInterval(() => void load(), refreshMs);
    return () => clearInterval(timer);
  }, [slug, load, refreshMs]);

  const runs = useMemo(() => data?.runs ?? [], [data]);
  const shown = useMemo(() => filterRuns(runs, filter), [runs, filter]);
  const branches = useMemo(() => choices(runs, (r) => r.branch), [runs]);
  const actors = useMemo(() => choices(runs, (r) => r.actor), [runs]);

  if (!slug) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
        <FolderOpen className="h-10 w-10 text-[var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold text-[var(--color-text-secondary)]">Nemáš vybraný projekt</h2>
        <p className="max-w-md text-xs text-[var(--color-text-muted)]">
          Repozitár je viazaný na projekt. Otvor <span className="font-mono">Projekty</span> a pripni projekt.
        </p>
        <button
          onClick={() => navigate("/projects")}
          className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 pointer-coarse:min-h-11"
        >
          Otvoriť Projekty
        </button>
      </div>
    );
  }

  function openTask(run: CiRun) {
    if (!run.task) return;
    setSelectedVersion({ versionId: run.task.version_id, versionNumber: run.task.version_number });
    navigate(`/riadiace-centrum?uloha=${encodeURIComponent(run.task.label)}`);
  }

  return (
    <div className="mx-auto max-w-5xl p-4 sm:p-6">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h1 className="text-base font-bold text-[var(--color-text-primary)]">Repozitár</h1>
        <button
          onClick={() => void load()}
          title="Obnoviť"
          className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] pointer-coarse:min-h-11"
        >
          <RefreshCw className="h-3.5 w-3.5" /> Obnoviť
        </button>
      </div>
      <p className="mb-4 text-xs text-[var(--color-text-muted)]">
        Behy kontrol (CI) projektu{" "}
        <span className="text-[var(--color-text-secondary)]">{data?.project_name ?? selectedProject?.name}</span> z
        GitHubu{data?.repo ? ` (${data.repo})` : ""}. Obnovujú sa samy každých {refreshMs / 1000} s
        {data?.fetched_at ? `, naposledy ${fmtDeployTime(data.fetched_at)}` : ""}.
      </p>

      {error && (
        <p
          role="alert"
          className="mb-3 rounded-lg bg-[var(--color-state-error-bg)] px-3 py-2 text-xs text-[var(--color-state-error-fg)]"
        >
          {error.message}
        </p>
      )}
      {data?.error && (
        <p
          role="status"
          className={`mb-3 rounded-lg px-3 py-2 text-xs ${
            data.stale
              ? "bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]"
              : "bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]"
          }`}
        >
          {data.stale
            ? `GitHub sa teraz nedá opýtať (${data.error}) — zobrazené behy sú z ${fmtDeployTime(data.fetched_at)}.`
            : data.error}
        </p>
      )}

      {!data && !error ? (
        <p className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Načítavam behy z GitHubu…
        </p>
      ) : (
        runs.length > 0 && (
          <>
            <div className="mb-2 grid grid-cols-1 gap-2 sm:grid-cols-4">
              <input
                type="search"
                value={filter.text}
                onChange={(e) => setFilter({ ...filter, text: e.target.value })}
                placeholder="Hľadať v behoch"
                aria-label="Hľadať v behoch"
                spellCheck={false}
                className={FIELD}
              />
              <select
                aria-label="Stav"
                value={filter.state}
                onChange={(e) => setFilter({ ...filter, state: e.target.value })}
                className={FIELD}
              >
                <option value="">Stav: všetky</option>
                {STATES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
              <select
                aria-label="Vetva"
                value={filter.branch}
                onChange={(e) => setFilter({ ...filter, branch: e.target.value })}
                className={FIELD}
              >
                <option value="">Vetva: všetky</option>
                {branches.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
              </select>
              <select
                aria-label="Kto"
                value={filter.actor}
                onChange={(e) => setFilter({ ...filter, actor: e.target.value })}
                className={FIELD}
              >
                <option value="">Kto: všetci</option>
                {actors.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </div>
            <p className="mb-3 text-xs text-[var(--color-text-muted)]">
              Behy: {shownCount(shown.length, runs.length)} najnovších · GitHub ich má spolu {data?.total_count ?? 0}.{" "}
              {data?.actions_url && (
                <a
                  href={data.actions_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[var(--color-text-link)] underline"
                >
                  Staršie behy na GitHube
                </a>
              )}
            </p>
            {shown.length === 0 ? (
              <p className="text-xs text-[var(--color-text-muted)]">Žiadny beh nezodpovedá výberu.</p>
            ) : (
              <ul className="flex flex-col gap-2" aria-label="Behy CI">
                {shown.map((run) => (
                  <RunRow key={run.id} run={run} onTask={() => openTask(run)} />
                ))}
              </ul>
            )}
          </>
        )
      )}
    </div>
  );
}

function RunRow({ run, onTask }: { run: CiRun; onTask: () => void }) {
  const look = STATE_LOOK[run.state] ?? UNKNOWN_LOOK;
  const Icon = look.icon;
  return (
    <li
      data-run={run.id}
      className="flex items-start gap-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] p-3"
    >
      <Icon className={`mt-0.5 h-4 w-4 flex-shrink-0 ${look.tone} ${look.spin ? "animate-spin" : ""}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <p className="min-w-0 break-words text-sm font-medium text-[var(--color-text-primary)]">{run.subject}</p>
          <a
            href={run.url}
            target="_blank"
            rel="noreferrer"
            aria-label={`Beh #${run.number} na GitHube`}
            title="Otvoriť na GitHube"
            className="flex flex-shrink-0 items-center justify-center rounded p-1 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] pointer-coarse:min-h-11 pointer-coarse:min-w-11"
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        </div>
        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
          <span className={`font-medium ${look.tone}`}>{run.state_label}</span> · {run.workflow} #{run.number} ·{" "}
          {run.event_label}
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-[var(--color-text-muted)]">
          <span className="flex min-w-0 items-center gap-1 break-all">
            <GitBranch className="h-3 w-3 flex-shrink-0" aria-hidden />
            {run.branch}
          </span>
          <span className="font-mono">{run.sha.slice(0, 7)}</span>
          <span>{run.actor}</span>
          <span>{fmtDeployTime(run.created_at)}</span>
          {run.duration_seconds !== null && run.duration_seconds !== undefined && (
            <span>{duration(run.duration_seconds)}</span>
          )}
        </p>
        {run.failed_step && (
          <p className="mt-1 break-words text-xs text-[var(--color-state-error-fg)]">Zlyhal krok: {run.failed_step}</p>
        )}
        {(run.task || run.deployed.length > 0) && (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {run.task && (
              <button
                type="button"
                onClick={onTask}
                title={run.task.title}
                className="rounded-full border border-[var(--color-accent-primary)] px-2 py-0.5 text-[11px] text-[var(--color-accent-primary)] hover:bg-[var(--color-surface-hover)] pointer-coarse:min-h-11 pointer-coarse:rounded-lg"
              >
                Úloha {run.task.label} · verzia {run.task.version_number}
              </button>
            )}
            {run.deployed.map((d) => (
              <span
                key={`${d.environment}-${d.customer}`}
                className="flex items-center gap-1 rounded-full bg-[var(--color-state-success-bg)] px-2 py-0.5 text-[11px] text-[var(--color-state-success-fg)]"
              >
                <Rocket className="h-3 w-3" aria-hidden />
                na {d.environment.toUpperCase()} · {d.customer} · {d.version}
              </span>
            ))}
          </div>
        )}
      </div>
    </li>
  );
}
