/**
 * Self-service change of the logged-in user's OWN password (v4.0.32).
 *
 * Reused by the "Moje konto" page and by the forced-change gate (an admin-set initial password the user
 * must change on first login). The backend verifies the current password and — because a password change
 * bumps ``token_version`` (killing the current JWT) — this form re-logs-in with the new password so the
 * user keeps a live session instead of being bounced to /login.
 */

import { useState } from "react";

import { ApiError } from "@/services/api";
import { changePasswordApi } from "@/services/api/users";
import { useAuthStore } from "@/store/authStore";

const INPUT_CLS =
  "w-full rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] px-3 py-2 " +
  "text-sm text-[var(--color-text-primary)] focus:border-[var(--color-accent-primary)] focus:outline-none";
const LABEL_CLS = "mb-1 block text-xs font-medium text-[var(--color-text-secondary)]";

export default function ChangeOwnPasswordForm({ onSuccess }: { onSuccess?: () => void }) {
  const user = useAuthStore((s) => s.user);
  const login = useAuthStore((s) => s.login);

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!user) return;
    if (next.length < 5) {
      setError("Nové heslo musí mať aspoň 5 znakov.");
      return;
    }
    if (next !== confirm) {
      setError("Nové heslá sa nezhodujú.");
      return;
    }
    if (next === current) {
      setError("Nové heslo musí byť iné ako súčasné.");
      return;
    }
    setBusy(true);
    try {
      await changePasswordApi(user.id, next, current);
      // The change bumped token_version → the current JWT is now dead. Re-login with the new password to
      // get a fresh session (+ an AuthUser with must_change_password=false) so the user stays in the app.
      await login(user.username, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      onSuccess?.();
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        // The backend returns a curated plain-Slovak reason (e.g. "Súčasné heslo je nesprávne.").
        setError(
          typeof err.message === "string" && err.message && err.message !== "[object Object]"
            ? err.message
            : "Súčasné heslo je nesprávne.",
        );
      } else if (err instanceof ApiError) {
        setError("Zmena hesla zlyhala. Skús to znova.");
      } else {
        setError("Sieťová chyba pri zmene hesla.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-sm space-y-3">
      {error && (
        <div className="rounded-lg border border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] px-3 py-2 text-xs text-[var(--color-state-error-fg)]">
          {error}
        </div>
      )}
      <div>
        <label className={LABEL_CLS} htmlFor="current-password">
          Súčasné heslo
        </label>
        <input
          spellCheck={false}
          id="current-password"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          className={INPUT_CLS}
          required
        />
      </div>
      <div>
        <label className={LABEL_CLS} htmlFor="new-password">
          Nové heslo
        </label>
        <input
          spellCheck={false}
          id="new-password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          className={INPUT_CLS}
          required
        />
      </div>
      <div>
        <label className={LABEL_CLS} htmlFor="confirm-password">
          Zopakuj nové heslo
        </label>
        <input
          spellCheck={false}
          id="confirm-password"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          className={INPUT_CLS}
          required
        />
      </div>
      <button
        type="submit"
        disabled={busy}
        className="rounded bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {busy ? "Ukladám…" : "Zmeniť heslo"}
      </button>
    </form>
  );
}
