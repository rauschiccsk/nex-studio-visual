/**
 * "Moje konto" — every user's self-service account page (v4.0.32).
 *
 * Shows the logged-in user's own details (read-only) and lets them change their OWN password. Reachable
 * by every role from the sidebar user card — the admin "Používatelia" tab (ri-only) manages OTHER users.
 */

import { useState } from "react";

import ChangeOwnPasswordForm from "@/components/auth/ChangeOwnPasswordForm";
import { useAuthStore } from "@/store/authStore";

// Slovak label per USER account role (ri/ha/shu) — mirrors the Sidebar + Settings labels.
const ROLE_LABELS: Record<string, string> = { ri: "Manažér", ha: "Medior", shu: "Junior" };

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-[var(--color-text-muted)]">{label}</div>
      <div className="text-sm text-[var(--color-text-primary)]">{value || "—"}</div>
    </div>
  );
}

export default function AccountPage() {
  const user = useAuthStore((s) => s.user);
  const [done, setDone] = useState(false);

  if (!user) return null;

  const fullName = [user.first_name, user.last_name].filter(Boolean).join(" ") || "—";

  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="text-base font-bold text-[var(--color-text-primary)]">Moje konto</h1>
      <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Tvoje údaje a zmena hesla.</p>

      <div className="mt-5 grid grid-cols-2 gap-4 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] p-4">
        <Field label="Meno" value={fullName} />
        <Field label="Používateľské meno" value={user.username} />
        <Field label="E-mail" value={user.email} />
        <Field label="Rola" value={ROLE_LABELS[user.role] ?? user.role} />
      </div>

      <div className="mt-6">
        <h2 className="mb-3 text-sm font-semibold text-[var(--color-text-primary)]">Zmena hesla</h2>
        {done ? (
          <div className="max-w-sm rounded-lg border border-[var(--color-state-success-bg)] bg-[var(--color-state-success-bg)] px-3 py-2 text-xs text-[var(--color-text-primary)]">
            Heslo bolo zmenené.
            <button
              type="button"
              onClick={() => setDone(false)}
              className="ml-2 text-[var(--color-text-link)] hover:underline"
            >
              Zmeniť znova
            </button>
          </div>
        ) : (
          <ChangeOwnPasswordForm onSuccess={() => setDone(true)} />
        )}
      </div>
    </div>
  );
}
