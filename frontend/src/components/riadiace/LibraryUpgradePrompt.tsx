/** What an upgrade offer shows: the version the app builds with, the newest, how far behind, „Čo prinesie“ and the
 *  parts that move together. */
export interface UpgradeOffer {
  current: string | null;
  latest: string | null;
  behind: number;
  changelog: { version: string; body: string }[];
  parts: { key: string; label: string; current: string | null }[];
}

/** The words of one kit's offer. */
export interface UpgradeWords {
  badge: string;
  title: string;
  subtitle: string;
  /** Under the parts, when there is more than one — that they move together. */
  partsNote: string;
  /** What the Manažér will see after the upgrade, and where. */
  previewNote: string;
}

/**
 * The opt-in upgrade of an ICC kit the app is behind on (#3; DEV-253) — shown when the Manažér founds a new version,
 * like a venv package upgrade. nex-shared and nex-app-kit say it in their own words (NexsharedUpgradePrompt,
 * AppKitUpgradePrompt); the comparison, the parts, „Čo prinesie“ and the two choices are the same. The parent renders
 * it only when the app is behind.
 */
export function LibraryUpgradePrompt({
  offer: status,
  words,
  busy = false,
  onUpgrade,
  onStay,
}: {
  offer: UpgradeOffer;
  words: UpgradeWords;
  busy?: boolean;
  onUpgrade: (target: string) => void;
  onStay: () => void;
}) {
  const target = status.latest ?? "";

  return (
    <div className="rounded-lg border border-[var(--color-accent-primary)]/40 bg-[var(--color-surface)] overflow-hidden">
      <div className="px-4 pt-3.5 pb-3 border-b border-[var(--color-border-default)]">
        <span className="inline-block font-mono text-[10px] tracking-wider uppercase px-2 py-0.5 rounded-full border border-[var(--color-accent-primary)]/40 text-[var(--color-accent-primary)]">
          {words.badge}
        </span>
        <h3 className="mt-2 text-[15px] font-semibold text-[var(--color-text-primary)]">
          {words.title}
        </h3>
        <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
          {words.subtitle}
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

        {/* DEV-161, DEV-253: more parts (two frontends; the frame and the server) — each one's version, all move together. */}
        {status.parts.length > 1 && (
          <div className="space-y-1">
            <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-[var(--color-text-secondary)]">
              {status.parts.map((part) => (
                <li key={part.key}>
                  {part.label}{" "}
                  <span className="font-mono text-[var(--color-text-primary)]">v{part.current ?? "?"}</span>
                </li>
              ))}
            </ul>
            <p className="text-[11px] text-[var(--color-text-muted)]">{words.partsNote}</p>
          </div>
        )}

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
          {words.previewNote}
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
