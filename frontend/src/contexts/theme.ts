/**
 * Theme context — the parts that are not a component (DESIGN.md § 3.3a dark mode).
 *
 * The provider lives in ``ThemeContext.tsx`` and the hook in ``hooks/useTheme.ts``. They are kept apart from the
 * provider because React's fast refresh can only replace a file that exports components and nothing else
 * (DEV-199 — the lint rule ``react-refresh/only-export-components``).
 */

import { createContext } from "react";

export interface ThemeContextValue {
  /** Whether dark mode is currently active. */
  isDark: boolean;
  /** Toggle between light and dark mode. */
  toggleDark: () => void;
}

export const ThemeContext = createContext<ThemeContextValue | undefined>(
  undefined,
);

/** Build the localStorage key scoped to the given username. */
export function darkModeKey(username: string): string {
  return `nex_dark_${username}`;
}
