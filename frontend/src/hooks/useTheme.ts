import { useContext } from "react";

import { ThemeContext, type ThemeContextValue } from "@/contexts/theme";

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
