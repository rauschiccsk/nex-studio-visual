/**
 * Forced initial-password change gate (v4.0.32).
 *
 * An admin creates a user with an INITIAL password (``must_change_password=true``). Until the user picks
 * their own password this gate blocks the whole app behind a change-password screen — they can only change
 * the password or log out. The self-service form re-logs-in on success, so ``must_change_password`` flips
 * to false and this gate releases to the app automatically.
 */

import { useNavigate } from "react-router-dom";

import { useAuthStore } from "@/store/authStore";

import ChangeOwnPasswordForm from "./ChangeOwnPasswordForm";

export default function ForcedPasswordGate({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();

  if (!user?.must_change_password) {
    return <>{children}</>;
  }

  async function handleLogout() {
    await logout();
    navigate("/login");
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-surface)] px-4">
      <div className="w-full max-w-sm rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface)] p-6 shadow-lg">
        <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">Zmeň si heslo</h1>
        <p className="mt-1 mb-4 text-sm text-[var(--color-text-secondary)]">
          Prihlásil si sa počiatočným heslom od správcu. Skôr než budeš pokračovať, nastav si vlastné heslo.
        </p>
        <ChangeOwnPasswordForm />
        <button
          type="button"
          onClick={handleLogout}
          className="mt-4 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] hover:underline"
        >
          Odhlásiť sa
        </button>
      </div>
    </div>
  );
}
