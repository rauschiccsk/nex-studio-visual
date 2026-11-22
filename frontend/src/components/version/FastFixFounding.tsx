// FastFixFounding — „Nová verzia“ → „Rýchla oprava“ (DEV-492): the fast fix is founded and waits; nothing starts.
//
// 22.11.2026 the Director founded a fast fix of Orthodox Hermenia from the project page only to have a version for a
// ticket, and it started at once with one sentence for its brief: „Kokpit robí zmätky … Treba mať jednotný postup a to
// najprv verzia a potom priradzovanie tiketov.“ A fast fix is now founded like any version; its tickets are put in on
// the „Plane tikety“ tab and it is started on its page — the tickets are its brief.
import { useState } from "react";
import { Loader2, Zap } from "lucide-react";

import WorkKindChoice from "@/components/common/WorkKindChoice";
import ErrorNote from "@/components/common/ErrorNote";
import { unsavedWorkOf, type UnsavedWorkRefusal } from "@/components/controlCenter/unsavedWork";
import { foundFastFixApi } from "@/services/api/versions";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import type { WorkKind } from "@/lib/workKind";
import type { Version } from "@/types/version";

interface Props {
  projectId: string;
  /** The tree has unsaved work or could not be checked — the page's guard above says what to do. */
  blocked: boolean;
  onFounded: (version: Version) => void;
  /** The engine found unsaved work after the page looked — the page's guard shows it. */
  onUnsaved: (refusal: UnsavedWorkRefusal) => void;
}

export default function FastFixFounding({ projectId, blocked, onFounded, onUnsaved }: Props) {
  const [workKind, setWorkKind] = useState<WorkKind | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  async function found() {
    if (!workKind || blocked || busy) return;
    setBusy(true);
    setError(null);
    try {
      onFounded(await foundFastFixApi(projectId, workKind));
    } catch (err: unknown) {
      const refusal = unsavedWorkOf(err);
      if (refusal) onUnsaved(refusal);
      else setError(humanizeApiError(err, "Založenie rýchlej opravy zlyhalo"));
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--color-text-secondary)]">
        Rýchla oprava dostane číslo ďalšej opravnej verzie — určí ho kokpit. Po založení do nej zaradíš tikety na karte
        „Plane tikety“ tlačidlom „Zaradiť do verzie“ a spustíš ju na jej stránke. Zadaním rýchlej opravy sú zaradené
        tikety; bez tiketu sa nespustí.
      </p>
      <WorkKindChoice name="fast-fix-kind" value={workKind} onChange={setWorkKind} disabled={busy} />
      <ErrorNote
        error={error}
        className="rounded-lg border border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] p-3"
      />
      <button
        type="button"
        onClick={found}
        disabled={!workKind || blocked || busy}
        title={
          blocked
            ? "Projekt má neuložené zmeny — najprv ich ulož alebo zahoď."
            : workKind
              ? undefined
              : "Najprv zvoľ druh práce — oprava chyby, zmena alebo dar"
        }
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Zap className="h-4 w-4" aria-hidden="true" />}
        Založiť rýchlu opravu
      </button>
    </div>
  );
}
