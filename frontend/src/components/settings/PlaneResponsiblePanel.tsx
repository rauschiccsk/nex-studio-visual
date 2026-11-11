// DEV-360 — at each account the Manažér sets who answers for its tickets in Plane.
//
// Director 11.11.2026 (NEX Archive founded under the account alex, „Tiket v DEV sa nezaložil“): „Máme ten istý problém
// ako minule.“ The cockpit looked for the responsible person of a ticket by the account's names among the people of
// the „Zodpovedný“ group of the DEV register; alex („Alexander Honest“) and rausch („Develop Rausch“) are the
// Director's and match nobody there. Now the person is chosen here; an account without one is matched by its names
// as before, and the row says whom they find.

import { useCallback, useEffect, useState } from "react";

import {
  listPlaneResponsibleApi,
  setPlaneResponsibleApi,
  type PlaneResponsibleAccount,
} from "@/services/api/users";
import { humanizeApiError } from "@/services/apiError";

/** The empty choice — what the account's names find, or that they find nobody. */
function byNameLabel(account: PlaneResponsibleAccount): string {
  return account.by_name
    ? `Podľa mena konta: ${account.by_name}`
    : "Nenastavený — meno konta v Plane nikoho nenájde";
}

export default function PlaneResponsiblePanel() {
  const [people, setPeople] = useState<string[]>([]);
  const [accounts, setAccounts] = useState<PlaneResponsibleAccount[]>([]);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const read = await listPlaneResponsibleApi();
      setPeople(read.people);
      setAccounts(read.accounts);
      setError(null);
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Zodpovedných v Plane sa nepodarilo načítať").message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function choose(account: PlaneResponsibleAccount, name: string) {
    setSaving(account.id);
    setError(null);
    try {
      const saved = await setPlaneResponsibleApi(account.id, name || null);
      setAccounts((rows) => rows.map((row) => (row.id === saved.id ? saved : row)));
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Zodpovedného sa nepodarilo uložiť").message);
    } finally {
      setSaving(null);
    }
  }

  return (
    <section className="mt-6 space-y-2" aria-labelledby="plane-responsible-title">
      <h3 id="plane-responsible-title" className="text-sm font-semibold text-[var(--color-text-primary)]">
        Zodpovedný v Plane
      </h3>
      <p className="text-xs text-[var(--color-text-muted)]">
        Kto za tikety konta zodpovedá v evidencii DEV — pri tikete projektu založeného z nápadu a pri tikete zo
        Zásobníka. Konto bez nastavenia kokpit páruje podľa jeho mena.
      </p>
      {error && (
        <p role="alert" className="text-xs text-[var(--color-state-error-fg)]">
          {error}
        </p>
      )}
      {accounts.length > 0 && (
        <table className="w-full text-xs">
          <tbody>
            {accounts.map((account) => (
              <tr key={account.id} className="border-b border-[var(--color-border-default)]">
                <td className="py-1.5 pr-3 text-[var(--color-text-primary)]">
                  {account.username}
                  {account.name && <span className="text-[var(--color-text-muted)]"> · {account.name}</span>}
                </td>
                <td className="py-1.5">
                  <select
                    aria-label={`Zodpovedný v Plane pre konto ${account.username}`}
                    value={account.chosen ?? ""}
                    disabled={saving !== null}
                    onChange={(e) => void choose(account, e.target.value)}
                    className="w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 text-[var(--color-text-primary)]"
                  >
                    <option value="">{byNameLabel(account)}</option>
                    {people.map((person) => (
                      <option key={person} value={person}>
                        {person}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
