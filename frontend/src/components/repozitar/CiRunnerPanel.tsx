import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, CircleAlert, Loader2, Play } from "lucide-react";

import { humanizeApiError, type HumanError } from "@/services/apiError";
import { getCiRunner, startCiRunner, type CiRunner } from "@/services/api/ciRunner";

/**
 * DEV-158 — where the project's CI runs. The cockpit gave a runner on ANDROS only to the projects it founded; NEX Inbox,
 * which it took over, ran its CI on GitHub's paid machines for four months and nobody saw it. The panel says whether
 * the project's own runner works, starts it with one click, and names every job that runs elsewhere.
 *
 * What it says and offers is the server's: the button is exactly what the start request accepts, never a guess here.
 */

/** While the runner starts, how often the tab asks how far it got. */
export const STARTING_POLL_MS = 5000;

export default function CiRunnerPanel({ slug, refreshKey }: { slug: string; refreshKey: number }) {
  const [runner, setRunner] = useState<CiRunner | null>(null);
  // Two errors: reading the runner again clears its own, never the reason a start was refused.
  const [loadError, setLoadError] = useState<HumanError | null>(null);
  const [startError, setStartError] = useState<HumanError | null>(null);
  const [sending, setSending] = useState(false);
  // An answer for a project no longer selected must not land on the one that is.
  const current = useRef(slug);
  current.current = slug;

  const load = useCallback(
    () =>
      // `Promise.resolve().then` — a missing function throws synchronously and would take the whole screen down.
      Promise.resolve()
        .then(() => getCiRunner(slug))
        .then((r) => {
          if (current.current !== slug) return;
          setRunner(r);
          setLoadError(null);
        })
        .catch((e: unknown) => {
          if (current.current === slug) setLoadError(humanizeApiError(e, "Zistenie vykonávača kontrol zlyhalo"));
        }),
    [slug],
  );

  useEffect(() => {
    setRunner(null);
    setLoadError(null);
    setStartError(null);
  }, [slug]);
  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const starting = runner?.starting ?? false;
  useEffect(() => {
    if (!starting) return;
    const timer = setInterval(() => void load(), STARTING_POLL_MS);
    return () => clearInterval(timer);
  }, [starting, load]);

  function start() {
    setSending(true);
    setStartError(null);
    Promise.resolve()
      .then(() => startCiRunner(slug))
      .then((r) => {
        if (current.current !== slug) return;
        setRunner(r);
      })
      .catch((e: unknown) => {
        setStartError(humanizeApiError(e, "Spustenie vykonávača kontrol zlyhalo"));
        // The refusal says why; the tab then shows what the runner is now.
        void load();
      })
      .finally(() => setSending(false));
  }

  if (!runner) {
    return (
      <section aria-label="Vykonávač kontrol" className="mb-4 text-xs text-[var(--color-text-muted)]">
        {loadError ? (
          <p role="alert" className="text-[var(--color-state-error-fg)]">
            {loadError.message}
          </p>
        ) : (
          <p className="flex items-center gap-2">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Zisťujem vykonávač kontrol…
          </p>
        )}
      </section>
    );
  }

  const Icon = runner.starting ? Loader2 : runner.healthy ? CheckCircle2 : CircleAlert;
  const tone = runner.healthy
    ? "text-[var(--color-state-success-fg)]"
    : runner.starting
      ? "text-[var(--color-text-secondary)]"
      : "text-[var(--color-state-warning-fg)]";

  return (
    <section
      aria-label="Vykonávač kontrol"
      className="mb-4 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] p-3 text-xs"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="flex min-w-0 items-start gap-2 text-[var(--color-text-primary)]">
          <Icon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${tone} ${runner.starting ? "animate-spin" : ""}`} />
          <span className="min-w-0 break-words">{runner.summary}</span>
        </p>
        {runner.action && (
          <button
            onClick={start}
            disabled={sending}
            className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-60 pointer-coarse:min-h-11"
          >
            {sending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            {runner.action}
          </button>
        )}
      </div>
      {runner.note && (
        <p role="status" className="mt-2 rounded bg-[var(--color-state-warning-bg)] px-2 py-1.5 text-[var(--color-state-warning-fg)]">
          {runner.note}
        </p>
      )}
      {[startError, loadError].map(
        (failed) =>
          failed && (
            <p
              key={failed.message}
              role="alert"
              className="mt-2 rounded bg-[var(--color-state-error-bg)] px-2 py-1.5 text-[var(--color-state-error-fg)]"
            >
              {failed.message}
            </p>
          ),
      )}
      {runner.jobs_elsewhere.length > 0 && (
        <div className="mt-3 rounded bg-[var(--color-state-warning-bg)] px-2 py-2 text-[var(--color-state-warning-fg)]">
          <p className="font-medium">Kontroly mimo vlastného vykonávača ({runner.jobs_elsewhere.length})</p>
          <p className="mt-1">
            Tieto úlohy CI nebežia na vykonávači <span className="font-mono">{runner.label}</span> na ANDROSe, ale na
            strojoch GitHubu alebo inde. Za minúty na strojoch GitHubu sa platí a kód sa kontroluje mimo ICC (rozhodnutie
            D-009). Zmeniť ich treba v projekte — novou verziou alebo rýchlou opravou.
          </p>
          <ul className="mt-1.5 space-y-0.5">
            {runner.jobs_elsewhere.map((job) => (
              <li key={`${job.workflow}/${job.job}`} className="break-words">
                <span className="font-mono">
                  {job.workflow} › {job.job}
                </span>{" "}
                — {job.runs_on}
              </li>
            ))}
          </ul>
        </div>
      )}
      {runner.unreadable_workflows.length > 0 && (
        <p className="mt-2 text-[var(--color-state-warning-fg)]">
          Nedá sa prečítať: <span className="font-mono">{runner.unreadable_workflows.join(", ")}</span> — kokpit nevie
          povedať, kde bežia jeho úlohy.
        </p>
      )}
    </section>
  );
}
