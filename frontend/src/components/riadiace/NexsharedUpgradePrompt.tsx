import type { NexsharedStatus } from "@/services/api/projects";

/**
 * Auto-notify nex-shared upgrade prompt (#3). Shown when the Manažér founds a new version and
 * the app is behind the latest published nex-shared — an opt-in, per-version bump (like a venv
 * package upgrade). Upgrading rewrites the app's pin so the new version's Vizuál preview + build
 * run on the chosen nex-shared. Never shown when up-to-date (the parent only renders it when
 * `status.behind > 0`).
 */
export function NexsharedUpgradePrompt({
  status,
  busy = false,
  onUpgrade,
  onStay,
}: {
  status: NexsharedStatus;
  busy?: boolean;
  onUpgrade: (target: string) => void;
  onStay: () => void;
}) {
  const target = status.latest ?? "";

  return (
    <div className="rounded-lg border border-[var(--color-accent-primary)]/40 bg-[var(--color-surface)] overflow-hidden">
      <div className="px-4 pt-3.5 pb-3 border-b border-[var(--color-border-default)]">
        <span className="inline-block font-mono text-[10px] tracking-wider uppercase px-2 py-0.5 rounded-full border border-[var(--color-accent-primary)]/40 text-[var(--color-accent-primary)]">
          nex-shared
        </span>
        <h3 className="mt-2 text-[15px] font-semibold text-[var(--color-text-primary)]">
          K dispozícii je novší nex-shared
        </h3>
        <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
          Táto appka je pozadu za spoločným dizajnovým kitom.
        </p>
      </div>

      <div className="px-4 py-3.5 space-y-3">
        {/* version compare */}
        <div className="flex items-center gap-3 rounded-md border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-3.5 py-2.5">
          <div className="flex flex-col">
            <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">Teraz</span>
            <span className="font-mono text-sm font-semibold text-[var(--color-text-primary)]">
              v{status.current ?? "?"}
            </span>
          </div>
          <span className="text-[var(--color-text-muted)]">→</span>
          <div className="flex flex-col">
            <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">Najnovšia</span>
            <span className="font-mono text-sm font-semibold text-[var(--color-accent-primary)]">v{target}</span>
          </div>
          <span className="ml-auto text-[11px] px-2 py-0.5 rounded-full bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)] whitespace-nowrap">
            {status.behind} {status.behind === 1 ? "verzia" : status.behind < 5 ? "verzie" : "verzií"} pozadu
          </span>
        </div>

        {/* "Čo prinesie" — the changelog delta */}
        {status.changelog.length > 0 && (
          <div className="space-y-1.5">
            <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">Čo prinesie</span>
            <div className="space-y-2">
              {status.changelog.map((entry) => (
                <div key={entry.version} className="text-[13px]">
                  <span className="font-mono text-xs text-[var(--color-text-secondary)]">v{entry.version}</span>
                  <div className="mt-0.5 whitespace-pre-wrap text-[var(--color-text-primary)]">{entry.body}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        <p className="text-[12px] text-[var(--color-text-muted)] border-l-2 border-[var(--color-status-success)] pl-2.5">
          Po povýšení uvidíš nový vzhľad hneď v náhľade Vizuál — to je zároveň tvoja vizuálna kontrola.
        </p>
      </div>

      <div className="flex gap-2.5 px-4 pb-4">
        <button
          type="button"
          disabled={busy}
          onClick={onStay}
          className="flex-1 text-[13px] font-medium py-2 rounded-md border border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-60"
        >
          Ostať na v{status.current ?? "?"}
        </button>
        <button
          type="button"
          disabled={busy || !target}
          onClick={() => onUpgrade(target)}
          className="flex-1 text-[13px] font-semibold py-2 rounded-md bg-[var(--color-accent-primary)] text-white hover:opacity-90 disabled:opacity-60"
        >
          {busy ? "Povyšujem…" : `Povýšiť na v${target}`}
        </button>
      </div>
    </div>
  );
}
