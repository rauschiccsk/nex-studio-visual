/**
 * DEV-342 — after „Zmazať natrvalo“ the list of projects says what the delete did beyond the project itself.
 *
 * 10.11.2026 Liturgikon was to be deleted „tak akoby ten projekt nebol založený“: its idea goes back to Nápady with its
 * talk, its ports and images go, and the GitHub repository goes — or stays and the Manažér is told why. Before, a
 * refused repository was written only to the server's log. What stayed and needs a hand is a warning and does not
 * disappear on its own; the state is read once and wiped, so a refresh does not show it again.
 */
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

export interface DeletedProjectState {
  deletedProject: { name: string; notes: string[]; leftBehind: string[] };
}

export default function DeletedProjectNotice() {
  const location = useLocation();
  const [deleted, setDeleted] = useState<DeletedProjectState["deletedProject"] | null>(null);

  useEffect(() => {
    const st = (location.state ?? null) as DeletedProjectState | null;
    if (st?.deletedProject) {
      setDeleted(st.deletedProject);
      window.history.replaceState({}, "");
    }
  }, [location.state]);

  if (!deleted) return null;
  const ok = deleted.leftBehind.length === 0;
  return (
    <div
      role="status"
      aria-label="Výsledok zmazania projektu"
      className={`mb-4 rounded-lg border px-4 py-3 ${
        ok
          ? "border-[var(--color-state-success-bg)] bg-[var(--color-state-success-bg)]"
          : "border-[var(--color-state-warning-bg)] bg-[var(--color-state-warning-bg)]"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <p className={`text-sm font-medium ${ok ? "text-[var(--color-state-success-fg)]" : "text-[var(--color-state-warning-fg)]"}`}>
          Projekt „{deleted.name}“ je zmazaný.
        </p>
        <button
          type="button"
          onClick={() => setDeleted(null)}
          className="text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
        >
          Zavrieť
        </button>
      </div>
      {deleted.notes.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-[12px] space-y-0.5 text-[var(--color-text-primary)]">
          {deleted.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      {!ok && (
        <>
          <p className="mt-2 text-[12px] font-medium text-[var(--color-state-warning-fg)]">Čo ostalo a treba doriešiť</p>
          <ul className="mt-1 list-disc pl-5 text-[12px] space-y-0.5 text-[var(--color-state-warning-fg)]">
            {deleted.leftBehind.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
