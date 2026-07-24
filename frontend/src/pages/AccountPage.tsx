/**
 * "Moje konto" — every user's self-service account page (v4.0.32 / v4.0.33).
 *
 * Shows the logged-in user's own details and lets them EDIT the safe fields (e-mail, name, Telegram id)
 * and change their OWN password. Reachable by every role from the sidebar user card — the admin
 * "Používatelia" tab (ri-only) manages OTHER users, including role + activation.
 */

import { useEffect, useState } from "react";

import ChangeOwnPasswordForm from "@/components/auth/ChangeOwnPasswordForm";
import { ApiError } from "@/services/api";
import { updateMyProfileApi } from "@/services/api/auth";
import { useAuthStore } from "@/store/authStore";

// Slovak label per USER account role (ri/ha/shu) — mirrors the Sidebar + Settings labels.
const ROLE_LABELS: Record<string, string> = { ri: "Manažér", ha: "Medior", shu: "Junior" };

const INPUT_CLS =
  "w-full rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] px-3 py-2 " +
  "text-sm text-[var(--color-text-primary)] focus:border-[var(--color-accent-primary)] focus:outline-none";
const LABEL_CLS = "mb-1 block text-xs font-medium text-[var(--color-text-secondary)]";

function ReadOnlyField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className={LABEL_CLS}>{label}</div>
      <div className="text-sm text-[var(--color-text-primary)]">{value || "—"}</div>
    </div>
  );
}

export default function AccountPage() {
  const user = useAuthStore((s) => s.user);
  const fetchMe = useAuthStore((s) => s.fetchMe);

  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [telegram, setTelegram] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pwDone, setPwDone] = useState(false);

  // Seed the form from the current user (and re-seed after a save refreshes the store).
  useEffect(() => {
    if (!user) return;
    setFirstName(user.first_name ?? "");
    setLastName(user.last_name ?? "");
    setEmail(user.email ?? "");
    setTelegram(user.telegram_chat_id ?? "");
  }, [user]);

  if (!user) return null;

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    if (!email.trim()) {
      setError("E-mail je povinný.");
      return;
    }
    setBusy(true);
    try {
      await updateMyProfileApi({
        email: email.trim(),
        first_name: firstName,
        last_name: lastName,
        telegram_chat_id: telegram,
      });
      await fetchMe(); // refresh the store so the sidebar display name updates too
      setSaved(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("Tento e-mail už používa iný používateľ.");
      } else if (err instanceof ApiError) {
        setError("Uloženie zlyhalo. Skús to znova.");
      } else {
        setError("Sieťová chyba pri ukladaní.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="text-base font-bold text-[var(--color-text-primary)]">Moje konto</h1>
      <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Tvoje údaje a zmena hesla.</p>

      {/* ── Editable details ─────────────────────────────────────────── */}
      <form
        onSubmit={handleSave}
        className="mt-5 space-y-4 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] p-4"
      >
        {error && (
          <div className="rounded-lg border border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] px-3 py-2 text-xs text-[var(--color-state-error-fg)]">
            {error}
          </div>
        )}
        {saved && !error && (
          <div className="rounded-lg border border-[var(--color-state-success-bg)] bg-[var(--color-state-success-bg)] px-3 py-2 text-xs text-[var(--color-text-primary)]">
            Údaje boli uložené.
          </div>
        )}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={LABEL_CLS} htmlFor="first-name">
              Meno
            </label>
            <input id="first-name" className={INPUT_CLS} value={firstName} onChange={(e) => setFirstName(e.target.value)} />
          </div>
          <div>
            <label className={LABEL_CLS} htmlFor="last-name">
              Priezvisko
            </label>
            <input id="last-name" className={INPUT_CLS} value={lastName} onChange={(e) => setLastName(e.target.value)} />
          </div>
          <div>
            <label className={LABEL_CLS} htmlFor="email">
              E-mail
            </label>
            <input id="email" type="email" className={INPUT_CLS} value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <label className={LABEL_CLS} htmlFor="telegram">
              Telegram chat ID
            </label>
            <input id="telegram" className={INPUT_CLS} value={telegram} onChange={(e) => setTelegram(e.target.value)} />
          </div>
          <ReadOnlyField label="Používateľské meno" value={user.username} />
          <ReadOnlyField label="Rola" value={ROLE_LABELS[user.role] ?? user.role} />
        </div>
        <button
          type="submit"
          disabled={busy}
          className="rounded bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? "Ukladám…" : "Uložiť"}
        </button>
      </form>

      {/* ── Password ─────────────────────────────────────────────────── */}
      <div className="mt-6">
        <h2 className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">Zmena hesla</h2>
        {pwDone ? (
          <div className="max-w-sm rounded-lg border border-[var(--color-state-success-bg)] bg-[var(--color-state-success-bg)] px-3 py-2 text-xs text-[var(--color-text-primary)]">
            Heslo bolo zmenené.
            <button
              type="button"
              onClick={() => setPwDone(false)}
              className="ml-2 text-[var(--color-text-link)] hover:underline"
            >
              Zmeniť znova
            </button>
          </div>
        ) : (
          <ChangeOwnPasswordForm onSuccess={() => setPwDone(true)} />
        )}
      </div>
    </div>
  );
}
