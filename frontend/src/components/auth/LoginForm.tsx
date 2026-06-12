/**
 * Reusable login form component — username + password fields with validation.
 *
 * Renders a controlled form with required-field validation. The parent
 * component provides the ``onSubmit`` callback and controls loading /
 * error state so the form itself stays presentation-only.
 */

import { useState, type FormEvent } from "react";
import { Button, Input } from "nex-shared";

export interface LoginFormProps {
  /** Called when the form passes client-side validation. */
  onSubmit: (username: string, password: string) => void;
  /** Disables the submit button and shows a spinner label. */
  loading?: boolean;
  /** Error message displayed above the submit button. */
  error?: string | null;
}

export default function LoginForm({
  onSubmit,
  loading = false,
  error = null,
}: LoginFormProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState({ username: false, password: false });

  const usernameError = touched.username && username.trim() === "";
  const passwordError = touched.password && password.trim() === "";

  function handleSubmit(e: FormEvent) {
    e.preventDefault();

    // Mark both fields as touched so validation errors show immediately.
    setTouched({ username: true, password: true });

    if (username.trim() === "" || password.trim() === "") {
      return;
    }

    onSubmit(username.trim(), password);
  }

  return (
    <form onSubmit={handleSubmit} noValidate data-testid="login-form">
      {/* Username */}
      <div className="mb-4">
        <label
          htmlFor="login-username"
          className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
        >
          Používateľské meno
        </label>
        <Input
          id="login-username"
          name="username"
          type="text"
          autoComplete="username"
          required
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          onBlur={() => setTouched((t) => ({ ...t, username: true }))}
          disabled={loading}
          invalid={usernameError}
          data-testid="login-username"
        />
        {usernameError && (
          <p className="mt-1 text-xs text-red-600" role="alert">
            Používateľské meno je povinné.
          </p>
        )}
      </div>

      {/* Password */}
      <div className="mb-4">
        <label
          htmlFor="login-password"
          className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
        >
          Heslo
        </label>
        <Input
          id="login-password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onBlur={() => setTouched((t) => ({ ...t, password: true }))}
          disabled={loading}
          invalid={passwordError}
          data-testid="login-password"
        />
        {passwordError && (
          <p className="mt-1 text-xs text-red-600" role="alert">
            Heslo je povinné.
          </p>
        )}
      </div>

      {/* Error toast / banner */}
      {error && (
        <div
          className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-400"
          role="alert"
          data-testid="login-error"
        >
          {error}
        </div>
      )}

      {/* Submit \u2014 uses the shared <Button> from nex-shared (E1 Phase B1, CR-NS-048). */}
      <Button
        type="submit"
        variant="primary"
        disabled={loading}
        className="w-full"
        data-testid="login-submit"
      >
        {loading ? "Prihlasovanie\u2026" : "Prihl\u00e1si\u0165 sa"}
      </Button>
    </form>
  );
}
