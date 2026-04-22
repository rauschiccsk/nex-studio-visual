/**
 * Theme context — DESIGN.md § 3.3a dark mode.
 *
 * Provides ``isDark`` boolean + ``toggleDark()`` action to the component
 * tree.  Persists per-user preference to ``localStorage`` under the key
 * ``nex_dark_{username}`` so each team member has an independent setting
 * on the same device.
 *
 * On every change the ``dark`` CSS class is added/removed from
 * ``<html>`` so Tailwind ``dark:`` variants activate immediately
 * (``darkMode: "class"``).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ThemeContextValue {
  /** Whether dark mode is currently active. */
  isDark: boolean;
  /** Toggle between light and dark mode. */
  toggleDark: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build the localStorage key scoped to the given username. */
export function darkModeKey(username: string): string {
  return `nex_dark_${username}`;
}

/** Read persisted dark-mode preference for *username*. Defaults to dark. */
function readPersistedDark(username: string | undefined): boolean {
  if (!username) return true;
  try {
    const stored = localStorage.getItem(darkModeKey(username));
    return stored === null ? true : stored === "true";
  } catch {
    return true;
  }
}

/** Apply or remove the ``dark`` class on ``<html>``. */
function applyDarkClass(isDark: boolean): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (isDark) {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export interface ThemeProviderProps {
  /** Currently logged-in username (from authStore). ``undefined`` when not
   *  authenticated — dark mode defaults to ``false`` in that case. */
  username: string | undefined;
  children: ReactNode;
}

export function ThemeProvider({ username, children }: ThemeProviderProps) {
  const [isDark, setIsDark] = useState<boolean>(() =>
    readPersistedDark(username),
  );

  // Re-read localStorage when the username changes (login / logout / switch).
  useEffect(() => {
    const next = readPersistedDark(username);
    setIsDark(next);
    applyDarkClass(next);
  }, [username]);

  // Keep ``<html>`` class in sync whenever ``isDark`` changes.
  useEffect(() => {
    applyDarkClass(isDark);
  }, [isDark]);

  const toggleDark = useCallback(() => {
    setIsDark((prev) => {
      const next = !prev;

      // Persist — if no username we still toggle in-memory, but don't
      // persist (there's no key).
      if (username) {
        try {
          localStorage.setItem(darkModeKey(username), String(next));
        } catch {
          // Storage full / disabled — ignore.
        }
      }

      return next;
    });
  }, [username]);

  const value = useMemo<ThemeContextValue>(
    () => ({ isDark, toggleDark }),
    [isDark, toggleDark],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Access the current theme context.
 *
 * Must be called inside ``<ThemeProvider>``.
 */
export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (ctx === undefined) {
    throw new Error("useTheme must be used within a <ThemeProvider>");
  }
  return ctx;
}
