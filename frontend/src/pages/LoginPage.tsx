/**
 * Login page — JWT login form (DESIGN.md § 3.1, route ``/login``).
 *
 * Renders the {@link LoginForm} component, delegates authentication to
 * ``authStore.login``, and redirects to ``/`` (or the ``?next=`` path)
 * on success. Shows an inline error banner on failure.
 */

import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import LoginForm from "@/components/auth/LoginForm";
import { useAuthStore } from "@/store/authStore";
import { ApiError } from "@/services/api";

function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const login = useAuthStore((s) => s.login);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(username: string, password: string) {
    setError(null);
    setLoading(true);

    try {
      await login(username, password);

      // Redirect to the page the user originally requested, or dashboard.
      const next = searchParams.get("next") ?? "/";
      navigate(next, { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setError("Invalid username or password.");
        } else {
          setError(err.message || "Login failed. Please try again.");
        }
      } else {
        setError("Network error. Please check your connection.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-full w-full items-center justify-center bg-gray-50 dark:bg-gray-900">
      <div className="w-full max-w-sm rounded-lg border border-gray-200 bg-white p-8 shadow-sm dark:border-gray-700 dark:bg-gray-800">
        <h1 className="mb-2 text-2xl font-semibold text-primary-700 dark:text-primary-400">
          NEX Studio
        </h1>
        <p className="mb-6 text-sm text-gray-600 dark:text-gray-400">
          Sign in to your account
        </p>
        <LoginForm
          onSubmit={handleSubmit}
          loading={loading}
          error={error}
        />
      </div>
    </div>
  );
}

export default LoginPage;
