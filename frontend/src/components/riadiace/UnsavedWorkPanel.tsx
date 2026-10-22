// DEV-175 — the New-Version page's Uložiť / Zahodiť wherever else founding a version was refused for unsaved work.
//
// The engine now refuses every founding of a version over unsaved work — from Dedo's brief, the „Rýchla oprava“
// dialog, a ticket's fast fix or Dedo's proposal on a build. Each of those screens shows this panel with the refusal,
// so the Manažér resolves it where he is, with one click, and then founds the version again.

import { useState } from "react";

import ErrorNote from "@/components/common/ErrorNote";
import { DirtyTreeGuard } from "@/components/riadiace/DirtyTreeGuard";
import { commitGitApi, discardGitApi, getGitStatusApi } from "@/services/api/projects";
import { humanizeApiError, type HumanError } from "@/services/apiError";

import type { UnsavedWorkRefusal } from "./unsavedWork";

const NOTE = "rounded-lg border border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] px-3 py-2";

export default function UnsavedWorkPanel({ refusal }: { refusal: UnsavedWorkRefusal }) {
  const [status, setStatus] = useState(refusal.status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const [resolved, setResolved] = useState(false);

  // The engine could not tell whether there is unsaved work — there is nothing to save or discard, only the sentence.
  if (!status) return <ErrorNote error={{ message: refusal.message }} className={NOTE} />;

  if (resolved) {
    return (
      <p role="status" className="text-xs text-[var(--color-state-success-fg)]">
        Neuložené zmeny sú vyriešené — klikni znova a verzia sa založí.
      </p>
    );
  }

  async function resolve(act: () => Promise<{ ok: boolean; error?: string }>, failed: string, phrase: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await act();
      if (!res.ok) {
        setError({ message: failed, detail: res.error });
        return;
      }
      const fresh = await getGitStatusApi(refusal.projectId);
      if (fresh.clean) {
        setResolved(true);
        return;
      }
      setStatus(fresh);
      setError({ message: "Projekt má stále neuložené zmeny." });
    } catch (err: unknown) {
      setError(humanizeApiError(err, phrase));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <DirtyTreeGuard
        status={status}
        busy={busy}
        onCommit={() =>
          resolve(
            () => commitGitApi(refusal.projectId),
            "Uloženie zmien zlyhalo — zmeny zostali neuložené.",
            "Uloženie zmien zlyhalo",
          )
        }
        onDiscard={() =>
          resolve(
            () => discardGitApi(refusal.projectId),
            "Zahodenie zmien zlyhalo — zmeny zostali v projekte.",
            "Zahodenie zmien zlyhalo",
          )
        }
      />
      <ErrorNote error={error} className={NOTE} />
    </div>
  );
}
