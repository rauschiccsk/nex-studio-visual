/**
 * Tests for ThemeContext — DESIGN.md § 3.3a dark mode.
 *
 * NEX Studio is **dark-by-default** (CR-NS-038/047): `readPersistedDark` returns
 * `true` when there is no stored preference (incl. no username). These tests
 * validate that dark-first default + per-user persistence + the `<html>.dark` class.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { ReactNode } from "react";
import {
  ThemeProvider,
  useTheme,
  darkModeKey,
} from "@/contexts/ThemeContext";

function wrapper(username: string | undefined) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <ThemeProvider username={username}>{children}</ThemeProvider>;
  };
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
});

describe("ThemeContext", () => {
  it("defaults to dark mode (isDark = true) when no persisted value", () => {
    const { result } = renderHook(() => useTheme(), { wrapper: wrapper("testuser") });
    expect(result.current.isDark).toBe(true);
  });

  it("toggleDark flips isDark from the dark default to light and back", () => {
    const { result } = renderHook(() => useTheme(), { wrapper: wrapper("testuser") });

    act(() => result.current.toggleDark());
    expect(result.current.isDark).toBe(false);

    act(() => result.current.toggleDark());
    expect(result.current.isDark).toBe(true);
  });

  it("persists the preference to localStorage on toggle", () => {
    const { result } = renderHook(() => useTheme(), { wrapper: wrapper("alice") });

    act(() => result.current.toggleDark()); // dark → light
    expect(localStorage.getItem(darkModeKey("alice"))).toBe("false");

    act(() => result.current.toggleDark()); // light → dark
    expect(localStorage.getItem(darkModeKey("alice"))).toBe("true");
  });

  it("uses a per-user localStorage key (nex_dark_{username})", () => {
    expect(darkModeKey("admin")).toBe("nex_dark_admin");
    expect(darkModeKey("tibor")).toBe("nex_dark_tibor");

    // alice explicitly LIGHT; bob has no preference → dark default.
    localStorage.setItem(darkModeKey("alice"), "false");

    const { result: alice } = renderHook(() => useTheme(), { wrapper: wrapper("alice") });
    const { result: bob } = renderHook(() => useTheme(), { wrapper: wrapper("bob") });

    expect(alice.current.isDark).toBe(false);
    expect(bob.current.isDark).toBe(true);
  });

  it("reads a persisted LIGHT preference on mount (overrides the dark default)", () => {
    localStorage.setItem(darkModeKey("zoltan"), "false");

    const { result } = renderHook(() => useTheme(), { wrapper: wrapper("zoltan") });

    expect(result.current.isDark).toBe(false);
  });

  it("applies / removes the dark class on <html> as isDark toggles", () => {
    const { result } = renderHook(() => useTheme(), { wrapper: wrapper("testuser") });

    // dark default → class present on mount
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    act(() => result.current.toggleDark());
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    act(() => result.current.toggleDark());
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("defaults to dark when username is undefined", () => {
    const { result } = renderHook(() => useTheme(), { wrapper: wrapper(undefined) });
    expect(result.current.isDark).toBe(true);
  });

  it("does not persist when username is undefined", () => {
    const { result } = renderHook(() => useTheme(), { wrapper: wrapper(undefined) });

    act(() => result.current.toggleDark()); // dark → light, in-memory only
    expect(result.current.isDark).toBe(false);
    expect(localStorage.length).toBe(0);
  });

  it("throws when useTheme is called outside ThemeProvider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useTheme())).toThrow(
      "useTheme must be used within a <ThemeProvider>",
    );
    spy.mockRestore();
  });
});
