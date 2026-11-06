import { useState } from "react";
import type { GitStatus } from "@/services/api/projects";

/**
 * Dirty-working-tree guard (v4.0.25). Shown on the New-Version page when the project has
 * uncommitted changes on disk. Founding is BLOCKED until the tree is clean — otherwise the
 * pipeline agent discovers the uncommitted work only in Príprava and asks the operator an
 * expert-level scope question they can't answer (Tibor/Nazar lens: the operator lands in a
 * question only Dedo could resolve). Offers a safe, guided resolution: commit (preserves
 * work — the default) or discard (destructive — behind an explicit confirm).
 */
export function DirtyTreeGuard({
  status,
  busy = false,
  onCommit,
  onDiscard,
}: {
  status: GitStatus;
  busy?: boolean;
  onCommit: () => void;
  onDiscard: () => void;
}) {
  const [showFiles, setShowFiles] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  const n = status.dirty_count;
  const word = n === 1 ? "zmena" : n < 5 ? "zmeny" : "zmien";

  return (
    <div className="rounded-lg border border-[var(--color-state-warning-fg)]/40 bg-[var(--color-surface)] overflow-hidden">
      <div className="px-4 pt-3.5 pb-3 border-b border-[var(--color-border-default)]">
        <span className="inline-flex items-center gap-1.5 font-mono text-[10px] tracking-wider uppercase px-2 py-0.5 rounded-full bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]">
          ⚠ neuložené zmeny
        </span>
        <h3 className="mt-2 text-[15px] font-semibold text-[var(--color-text-primary)]">
          Projekt má {n} {word}, ktoré neboli uložené
        </h3>
        <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
          Asi sa nedokončila predošlá práca. Pred založením verzie ich treba vyriešiť — inak by
          systém nevedel, čo do novej verzie patrí.
        </p>
      </div>

      <div className="px-4 py-3.5 space-y-3">
        <button
          type="button"
          onClick={() => setShowFiles((s) => !s)}
          className="text-[12px] text-[var(--color-text-link)] hover:underline"
        >
          {showFiles ? "Skryť zmeny" : "Zobraziť zmeny"}
        </button>
        {showFiles && (
          <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-canvas)] px-3 py-2 max-h-52 overflow-y-auto space-y-0.5">
            {status.files.map((f) => (
              <div key={f.path} className="flex items-center gap-2 text-[12px] font-mono">
                <span className="w-7 shrink-0 text-[var(--color-text-muted)]">{f.code}</span>
                <span className="text-[var(--color-text-secondary)] truncate">{f.path}</span>
              </div>
            ))}
            {status.truncated && (
              <div className="pt-1 text-[11px] text-[var(--color-text-muted)]">
                … a ďalšie (zobrazených prvých {status.files.length})
              </div>
            )}
          </div>
        )}
      </div>

      <div className="flex gap-2.5 px-4 pb-4">
        {confirmDiscard ? (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirmDiscard(false)}
              className="flex-1 rounded-md border border-[var(--color-border-default)] py-2 text-[13px] font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-60"
            >
              Späť
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onDiscard}
              className="flex-1 rounded-md bg-[var(--color-status-error)] py-2 text-[13px] font-semibold text-white hover:opacity-90 disabled:opacity-60"
            >
              {busy ? "Zahadzujem…" : `Naozaj zahodiť ${n} ${word}`}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirmDiscard(true)}
              className="flex-1 rounded-md border border-[var(--color-border-default)] py-2 text-[13px] font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-60"
            >
              Zahodiť
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onCommit}
              className="flex-1 rounded-md bg-[var(--color-accent-primary)] py-2 text-[13px] font-semibold text-white hover:opacity-90 disabled:opacity-60"
            >
              {busy ? "Ukladám…" : "Uložiť ich"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
