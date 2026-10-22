/**
 * DEV-72 — the settings the app asks of each customer, in the customer form.
 *
 * The app marks them in its `.env.example` (`@customer`, `@customer-secret`, `required`, `@public-url`); the cockpit
 * reads that and shows one field per setting with the app's own words. A secret is write-only: the stored value is
 * never shown, only that one is stored — an empty field keeps it. The public address is filled in by the cockpit at
 * deploy, so it is named, not asked for.
 */

import { customerSettings } from "@/lib/customerSettings";
import type { CustomerSettingsContract } from "@/types/customer";

const INPUT =
  "mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1.5 text-sm text-[var(--color-text-primary)]";

interface Props {
  contract: CustomerSettingsContract | null;
  values: Record<string, string>;
  onValue: (key: string, value: string) => void;
  secrets: Record<string, string>;
  onSecret: (key: string, value: string) => void;
  /** Secrets that already have a stored value (editing an existing customer). */
  storedSecrets: string[];
}

export default function CustomerSettingsFields({ contract, values, onValue, secrets, onSecret, storedSecrets }: Props) {
  const fields = customerSettings(contract);
  // DEV-164: a Web project's administration has an address of its own — the cockpit fills it in like the web's.
  const publicUrl = (contract?.settings ?? []).filter((s) => s.kind === "public_url" || s.kind === "public_url_admin");
  const problems = contract?.problems ?? [];
  if (fields.length === 0 && publicUrl.length === 0 && problems.length === 0) return null;

  return (
    <fieldset className="space-y-3 rounded-lg border border-[var(--color-border-default)] p-3">
      <legend className="px-1 text-xs font-semibold text-[var(--color-text-secondary)]">
        Nastavenia aplikácie pre tohto zákazníka
      </legend>
      {problems.length > 0 && (
        <p role="alert" className="text-[11px] text-[var(--color-state-error-fg)]">
          Aplikácia má v nastaveniach chybu, nasadenie sa nespustí, kým ju neopraví: {problems.join("; ")}.
        </p>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {fields.map((s) => {
          const secret = s.kind === "customer_secret";
          const stored = secret && storedSecrets.includes(s.key);
          return (
            <label key={s.key} className="block text-xs">
              <span className="text-[var(--color-text-secondary)]">
                {s.label}
                {s.required && (
                  <span className="text-[var(--color-state-error-fg)]" title="Povinné — bez neho sa nasadenie nespustí">
                    {" "}
                    *
                  </span>
                )}
              </span>
              <input
                spellCheck={false}
                type={secret ? "password" : "text"}
                autoComplete={secret ? "new-password" : "off"}
                value={secret ? (secrets[s.key] ?? "") : (values[s.key] ?? "")}
                onChange={(e) => (secret ? onSecret(s.key, e.target.value) : onValue(s.key, e.target.value))}
                placeholder={
                  secret
                    ? stored
                      ? "uložené — prázdne = bez zmeny"
                      : "uloží sa bezpečne; späť sa už nezobrazí"
                    : undefined
                }
                className={INPUT}
              />
              <span className="mt-1 block font-mono text-[10px] text-[var(--color-text-muted)]">{s.key}</span>
            </label>
          );
        })}
      </div>
      {publicUrl.length > 0 && (
        <p className="text-[11px] text-[var(--color-text-muted)]">
          Adresu aplikácie ({publicUrl.map((s) => s.key).join(", ")}) dopĺňa kokpit sám pri nasadení — presne tú, na
          ktorej inštalácia beží.
        </p>
      )}
      {fields.some((s) => s.required) && (
        <p className="text-[11px] text-[var(--color-text-muted)]">* povinné — bez nich sa nasadenie nespustí.</p>
      )}
    </fieldset>
  );
}
