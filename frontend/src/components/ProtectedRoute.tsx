import { Navigate, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

interface ProtectedRouteProps {
  children: ReactNode;
}

/**
 * Route guard that redirects unauthenticated users to /login.
 *
 * The authentication check is deliberately lightweight for now — it reads the
 * `nex_studio_token` key from localStorage. When the authStore is introduced
 * in a later task (DESIGN.md § 3.3), this component will be refactored to
 * read from the Zustand store instead.
 */
function ProtectedRoute({ children }: ProtectedRouteProps) {
  const location = useLocation();
  const token =
    typeof window !== "undefined"
      ? window.localStorage.getItem("nex_studio_token")
      : null;

  if (!token) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <>{children}</>;
}

export default ProtectedRoute;
