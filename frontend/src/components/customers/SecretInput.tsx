/**
 * DEV-437 — a customer secret on the customer card: asterisks, and for the Administrator an eye that shows it.
 *
 * The Director, 19.11.2026: „ak zadám token majú tam byť stále hviezdičky … a na konci riadku možnosť zobrazenia
 * daného tokenu, toto bude mať len Administrátor.“ A stored secret reads as asterisks — always as many, they do not
 * tell its length. The secret leaves the vault only when the eye is clicked (the cockpit records who saw it); showing
 * it is no change: only what is typed is saved, an empty field keeps the stored one.
 */

import { Eye, EyeOff, Loader2 } from "lucide-react";
import { useState } from "react";

/** What a stored secret reads as — the same for every secret, whatever its length. */
const STORED_MASK = "••••••••••";

interface Props {
  /** What was typed — the change; empty = keep the stored secret. */
  value: string;
  onChange: (value: string) => void;
  /** Whether the customer already has this secret stored. */
  stored: boolean;
  /** The hint while nothing is stored. */
  newPlaceholder: string;
  /** Present for the Administrator only — fetches the stored secret. */
  reveal?: () => Promise<string>;
  label: string;
  className: string;
}

export default function SecretInput({ value, onChange, stored, newPlaceholder, reveal, label, className }: Props) {
  const [shown, setShown] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const canReveal = stored && reveal !== undefined;
  const visible = shown !== null;

  async function toggle() {
    if (visible) {
      setShown(null);
      return;
    }
    if (!reveal) return;
    setBusy(true);
    setFailed(false);
    try {
      setShown(await reveal());
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <span className="relative block">
        <input
          spellCheck={false}
          type={visible ? "text" : "password"}
          autoComplete="new-password"
          value={value !== "" ? value : (shown ?? "")}
          onChange={(e) => onChange(e.target.value)}
          placeholder={stored ? STORED_MASK : newPlaceholder}
          title={stored ? "Uložené — prázdne pole = bez zmeny" : undefined}
          aria-label={label}
          className={canReveal ? `${className} pr-8` : className}
        />
        {canReveal && (
          <button
            type="button"
            onClick={() => void toggle()}
            disabled={busy}
            aria-label={visible ? `Skryť: ${label}` : `Zobraziť: ${label}`}
            title={visible ? "Skryť" : "Zobraziť uložené tajomstvo"}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] disabled:opacity-40"
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : visible ? (
              <EyeOff className="h-3.5 w-3.5" />
            ) : (
              <Eye className="h-3.5 w-3.5" />
            )}
          </button>
        )}
      </span>
      {failed && (
        <span role="alert" className="mt-1 block text-[11px] text-[var(--color-state-error-fg)]">
          Uložené tajomstvo sa nepodarilo načítať.
        </span>
      )}
    </>
  );
}
