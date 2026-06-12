/**
 * Dark mode integration test — verifies the ``dark`` CSS class on ``<html>``
 * tracks ``isDark`` (Tailwind v4 `@custom-variant dark`). NEX Studio is
 * dark-by-default (CR-NS-038/047): no stored preference → dark.
 */

import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { ReactNode } from "react";
import {
  ThemeProvider,
  useTheme,
  darkModeKey,
} from "@/contexts/ThemeContext";

function wrapper(username: string) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <ThemeProvider username={username}>{children}</ThemeProvider>;
  };
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
});

describe("Dark mode class strategy", () => {
  it("has the 'dark' class on <html> by default, removes it when toggled to light", () => {
    const { result } = renderHook(() => useTheme(), { wrapper: wrapper("testuser") });

    // Dark-by-default → class present on mount.
    expect(result.current.isDark).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    act(() => result.current.toggleDark());
    expect(result.current.isDark).toBe(false);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("re-adds the 'dark' class when toggled back to dark", () => {
    const { result } = renderHook(() => useTheme(), { wrapper: wrapper("testuser") });

    act(() => result.current.toggleDark()); // dark → light
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    act(() => result.current.toggleDark()); // light → dark
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("applies dark class on mount when persisted preference is true", () => {
    localStorage.setItem(darkModeKey("darkuser"), "true");

    const { result } = renderHook(() => useTheme(), { wrapper: wrapper("darkuser") });

    expect(result.current.isDark).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("does not apply dark class on mount when persisted preference is false", () => {
    localStorage.setItem(darkModeKey("lightuser"), "false");

    const { result } = renderHook(() => useTheme(), { wrapper: wrapper("lightuser") });

    expect(result.current.isDark).toBe(false);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("dark class state is independent per user (explicit-light user vs dark default)", () => {
    // userA explicitly LIGHT; userB has no preference → dark default.
    localStorage.setItem(darkModeKey("userA"), "false");

    const { result: resultA } = renderHook(() => useTheme(), { wrapper: wrapper("userA") });
    expect(resultA.current.isDark).toBe(false);
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    const { result: resultB } = renderHook(() => useTheme(), { wrapper: wrapper("userB") });
    expect(resultB.current.isDark).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
