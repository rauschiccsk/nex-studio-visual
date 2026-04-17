/**
 * PortField — number input with async availability validation and
 * "Suggest Next" button.
 *
 * Validates on blur by calling {@link checkPortAvailability}.  When a
 * conflict is detected the field shows an error and enables a "Suggest
 * Next" button that fetches the next free port from the backend.
 */

import { useState, useCallback, useRef } from "react";

import {
  checkPortAvailability,
  suggestNextAvailablePort,
} from "@/services/api/port-registry";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type PortType = "backend" | "frontend" | "db";

export interface PortFieldProps {
  /** Label displayed above the input. */
  label: string;
  /** Which service type this port belongs to (used for suggestion). */
  type: PortType;
  /** Current string value (controlled input). */
  value: string;
  /** Called when the value changes. */
  onChange: (value: string) => void;
  /** Placeholder text. */
  placeholder?: string;
  /** Disables input + buttons when true. */
  disabled?: boolean;
  /** data-testid for the wrapping container. */
  testId?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function PortField({
  label,
  type,
  value,
  onChange,
  placeholder,
  disabled = false,
  testId,
}: PortFieldProps) {
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [suggesting, setSuggesting] = useState(false);

  // Abort controller for in-flight requests
  const abortRef = useRef<AbortController | null>(null);

  // -- Blur validation -------------------------------------------------------
  const handleBlur = useCallback(async () => {
    const num = parseInt(value, 10);
    if (!value || isNaN(num)) {
      setError(null);
      return;
    }

    if (num < 1 || num > 65535) {
      setError("Port must be 1–65535");
      return;
    }

    // Cancel any previous in-flight check
    abortRef.current?.abort();
    abortRef.current = new AbortController();

    setChecking(true);
    setError(null);

    try {
      const available = await checkPortAvailability(num);
      if (!available) {
        setError(`Port ${num} is already in use`);
      } else {
        setError(null);
      }
    } catch {
      // Network error or aborted — don't show error to avoid noise
      setError(null);
    } finally {
      setChecking(false);
    }
  }, [value]);

  // -- Suggest next port -----------------------------------------------------
  const handleSuggest = useCallback(async () => {
    setSuggesting(true);
    try {
      const suggested = await suggestNextAvailablePort(type);
      onChange(String(suggested));
      setError(null);
    } catch {
      setError("Failed to get suggestion");
    } finally {
      setSuggesting(false);
    }
  }, [type, onChange]);

  // -- Styles ----------------------------------------------------------------
  const inputClass = `w-full rounded-md border px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 dark:bg-gray-800 dark:text-gray-100 ${
    error
      ? "border-red-500 focus:ring-red-500"
      : "border-gray-300 focus:ring-primary-500 dark:border-gray-600"
  }`;

  return (
    <div data-testid={testId}>
      <label
        htmlFor={testId}
        className="mb-1 block text-xs text-gray-500 dark:text-gray-400"
      >
        {label}
      </label>

      <div className="flex gap-2">
        <input
          id={testId}
          type="number"
          min={1}
          max={65535}
          value={value}
          onChange={(e) => {
            onChange(e.target.value);
            // Clear error on change — will revalidate on blur
            if (error) setError(null);
          }}
          onBlur={handleBlur}
          disabled={disabled}
          placeholder={placeholder}
          className={inputClass}
          data-testid={testId ? `${testId}-input` : undefined}
        />

        <button
          type="button"
          onClick={handleSuggest}
          disabled={disabled || suggesting}
          className="shrink-0 rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-600 dark:text-gray-400 dark:hover:bg-gray-700"
          data-testid={testId ? `${testId}-suggest` : undefined}
        >
          {suggesting ? "…" : "Suggest Next"}
        </button>
      </div>

      {checking && (
        <p
          className="mt-1 text-xs text-gray-400"
          data-testid={testId ? `${testId}-checking` : undefined}
        >
          Checking availability…
        </p>
      )}

      {error && (
        <p
          className="mt-1 text-xs text-red-600"
          role="alert"
          data-testid={testId ? `${testId}-error` : undefined}
        >
          {error}
        </p>
      )}
    </div>
  );
}
