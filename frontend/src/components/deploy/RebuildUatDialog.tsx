// RebuildUatDialog — DEV-238: „Postaviť nanovo“ on a UAT installation, after an explicit consent.
//
// A redeploy keeps an installation's settings (ICCINT-133); an installation an earlier deploy left broken
// (Author Asistent, 26.10.2026) could not be built again from the screen. Built anew, it loses its database and
// stored data — so the dialog says that in plain words and runs only once the Manažér ticks that he understands.
// Production installations never get this (the backend refuses it too).
import { useState } from "react";

interface RebuildUatDialogProps {
  customerName: string;
  versionLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
}

export default function RebuildUatDialog({ customerName, versionLabel, onCancel, onConfirm }: RebuildUatDialogProps) {
  const [agreed, setAgreed] = useState(false);

  return (
    <div
      className="fixed inset-0 z-50 flex overflow-y-auto bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rebuild-uat-title"
    >
      <div className="m-auto w-full max-w-lg rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-5 shadow-xl">
        <h2 id="rebuild-uat-title" className="text-sm font-semibold text-[var(--color-text-primary)]">
          Postaviť UAT nanovo — {customerName}
        </h2>
        <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
          Inštalácia UAT sa zruší aj s databázou a uloženými dátami a postaví sa nanovo z verzie {versionLabel}.
          Čo si v nej doteraz zadal na skúšanie, sa nevráti. Zaznačené body skúšky v kokpite ostávajú.
        </p>
        <p className="mt-2 text-xs text-[var(--color-text-muted)]">
          Použi to, keď je inštalácia pokazená a bežné „Nasadiť“ ju neopraví. Ostrých inštalácií sa to netýka.
        </p>
        <label className="mt-4 flex items-start gap-2 text-sm text-[var(--color-text-primary)]">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
          />
          Rozumiem, dáta tejto inštalácie UAT sa zahodia.
        </label>
        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]"
          >
            Zrušiť
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={!agreed}
            title={agreed ? undefined : "Najprv potvrď, že dáta tejto inštalácie UAT sa môžu zahodiť."}
            className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Zahodiť dáta a postaviť nanovo
          </button>
        </div>
      </div>
    </div>
  );
}
