/**
 * Protected route wrapper — DESIGN.md § 3.1.
 *
 * Since E1 Phase C (CR-NS-052) the guard logic lives in the shared
 * ``ProtectedRoute`` (nex-shared, router-agnostic); this thin wrapper supplies
 * the NEX-Studio specifics: the auth-store reads (token + fetchMe revalidation)
 * and the react-router redirect element (``<Navigate to="/login">`` preserving
 * the originating location in ``state.from`` for the next-param bounce-back).
 */

import { Navigate, useLocation } from "react-router-dom";
import { ProtectedRoute as SharedProtectedRoute } from "nex-shared";

import { useAuthStore } from "@/store/authStore";

interface ProtectedRouteProps {
  children: React.ReactNode;
}

export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  const token = useAuthStore((s) => s.token);
  const fetchMe = useAuthStore((s) => s.fetchMe);
  const location = useLocation();

  return (
    <SharedProtectedRoute
      authed={Boolean(token)}
      validate={fetchMe}
      // Re-read after fetchMe — the token may have been cleared if it was expired.
      isAuthed={() => Boolean(useAuthStore.getState().token)}
      redirect={<Navigate to="/login" state={{ from: location }} replace />}
    >
      {children}
    </SharedProtectedRoute>
  );
}
