// ZadanieConflictPanel — the Zadanie on disk is not the one the editor started from (DEV-40).
//
// The engine refuses to write over text the Manažér never saw (ICCINT-71) and sends that text with its 409. This
// panel shows it and lets him decide — replace it with his text, add his text after it, or take it into the
// editor. Nothing is written until he clicks.

import type { ZadanieClash } from "./zadanieClash";

interface Props {
  clash: ZadanieClash;
  busy?: boolean;
  onReplace: () => void;
  onAppend: () => void;
  onTakeOver: () => void;
}

const BUTTON =
  "rounded-lg border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50 transition-colors";

export default function ZadanieConflictPanel({ clash, busy = false, onReplace, onAppend, onTakeOver }: Props) {
  return (
    <div className="mt-2 space-y-2 rounded-lg border border-[var(--color-state-warning-fg)]/40 bg-[var(--color-state-warning-bg)] p-3">
      <p className="text-sm text-[var(--color-text-primary)]">{clash.message}</p>
      <p className="text-xs text-[var(--color-text-secondary)]">Na disku leží toto zadanie:</p>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-[var(--color-canvas)] p-2 text-xs text-[var(--color-text-primary)]">
        {clash.existing}
      </pre>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BUTTON} disabled={busy} onClick={onReplace}>
          Nahradiť mojím textom
        </button>
        <button type="button" className={BUTTON} disabled={busy} onClick={onAppend}>
          Doplniť môj text na koniec
        </button>
        <button type="button" className={BUTTON} disabled={busy} onClick={onTakeOver}>
          Prevziať text z disku do poľa
        </button>
      </div>
    </div>
  );
}
