// DEV-483 — at each account the admin switches the fast path with Poradca.
//
// Director 21.11.2026: „Chcem aby táto možnosť bola nastaviteľná pre každého používateľa osobitne. Dôvodom je že pre
// Tibora a Nazara chcem zatiaľ ponechať terajší spôsob, aby jednotlivé otázky riešili po jednom.“ Off by default; on,
// the Staviteľ's questions of that account's projects come at once, Poradca assesses them all and a summary waits.

import { useCallback, useEffect, useState } from "react";

import {
  listBatchReviewAccountsApi,
  setBatchReviewApi,
  type BatchReviewAccount,
} from "@/services/api/batchReview";
import { humanizeApiError } from "@/services/apiError";

export default function BatchReviewPanel() {
  const [accounts, setAccounts] = useState<BatchReviewAccount[]>([]);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setAccounts(await listBatchReviewAccountsApi());
      setError(null);
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Nastavenie rýchlej cesty s Poradcom sa nepodarilo načítať").message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggle(account: BatchReviewAccount) {
    setSaving(account.id);
    setError(null);
    try {
      const saved = await setBatchReviewApi(account.id, !account.on);
      setAccounts((rows) => rows.map((row) => (row.id === saved.id ? saved : row)));
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Nastavenie sa nepodarilo uložiť").message);
    } finally {
      setSaving(null);
    }
  }

  return (
    <section className="mt-6 space-y-2" aria-labelledby="batch-review-title">
      <h3 id="batch-review-title" className="text-sm font-semibold text-[var(--color-text-primary)]">
        Otázky Staviteľa najprv s Poradcom
      </h3>
      <p className="text-xs text-[var(--color-text-muted)]">
        Zapnuté: v projektoch konta Staviteľ položí otázky naraz ako karty, Poradca ich všetky posúdi a v Riadiacom
        centre čaká súhrn — zhody schváliš jedným klikom, ostatné rozhodneš na kartách. Vypnuté: otázky po jednej ako
        doteraz.
      </p>
      {error && (
        <p role="alert" className="text-xs text-[var(--color-state-error-fg)]">
          {error}
        </p>
      )}
      {accounts.length > 0 && (
        <ul className="divide-y divide-[var(--color-border-default)] text-xs">
          {accounts.map((account) => (
            <li key={account.id} className="flex items-center justify-between py-1.5">
              <span className="text-[var(--color-text-primary)]">
                {account.username}
                {account.name && <span className="text-[var(--color-text-muted)]"> · {account.name}</span>}
              </span>
              <label className="flex items-center gap-2 text-[var(--color-text-secondary)]">
                <input
                  type="checkbox"
                  checked={account.on}
                  disabled={saving !== null}
                  onChange={() => void toggle(account)}
                  aria-label={`Otázky Staviteľa najprv s Poradcom pre konto ${account.username}`}
                />
                {account.on ? "zapnuté" : "vypnuté"}
              </label>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
