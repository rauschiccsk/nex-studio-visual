/**
 * Protected route wrapper — DESIGN.md § 3.1.
 *
 * Checks ``authStore.token``:
 *   - If null → redirect to ``/login``
 *   - If present → call ``authStore.fetchMe()`` on mount to revalidate the
 *     session, then render children.
 *
 * Wrap any ``<Route>`` element that requires authentication with this
 * component (see ``App.tsx``).
 */

import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuthStore } from "@/store/authStore";

interface ProtectedRouteProps {
  children: React.ReactNode;
}

export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  const token = useAuthStore((s) => s.token);
  const fetchMe = useAuthStore((s) => s.fetchMe);
  const location = useLocation();

  // Track whether the initial fetchMe call has completed so we don't
  // flash protected content before the token is validated.
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (token) {
      fetchMe().finally(() => setReady(true));
    } else {
      setReady(true);
    }
    // Run once on mount (or when token changes from null → string).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Still loading — show nothing (avoids flash of redirect or content).
  if (!ready) return null;

  // After fetchMe, re-read token — it may have been cleared if the
  // token was expired/invalid.
  const currentToken = useAuthStore.getState().token;

  if (!currentToken) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
