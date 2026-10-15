/**
 * DEV-93 — the top bar names every page of the cockpit, and a new page without a name turns this red.
 *
 * 12.10.2026: the getting-started, adviser and preview pages showed only the product name in the top bar, so a
 * person did not know where they were. The names are a hand-written list beside the router, and a new page fell out
 * of it unnoticed — the notes page had the same gap until it was added by hand. This guard takes the pages from the
 * router itself.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

vi.mock("@/contexts/ThemeContext", () => ({ useTheme: () => ({ isDark: false, toggleDark: vi.fn() }) }));

import Topbar from "@/components/layout/Topbar";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

/** Every page the router renders inside the cockpit layout — redirects and the login page left out. */
function pages(): string[] {
  const app = readFileSync(resolve(SRC, "App.tsx"), "utf8");
  const found = ["/"]; // the index route
  for (const [, path, element] of app.matchAll(/<Route\s+path="([^"]+)"\s+element=\{<(\w+)/g)) {
    if (!path || element === "Navigate" || path === "*" || path === "/login") continue;
    found.push(`/${path.replace(/:[^/]+/g, "x")}`);
  }
  return found;
}

function topbarAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Topbar />
    </MemoryRouter>,
  );
}

/** The name the top bar shows at `path`. */
function nameAt(path: string): string {
  const { unmount } = topbarAt(path);
  const name = screen.getByText("/").nextElementSibling?.textContent ?? "";
  unmount();
  return name;
}

/** What a page without a name of its own shows — measured, so a changed fallback text cannot hide a gap. */
const FALLBACK = () => nameAt("/no-such-page-in-the-cockpit");

describe("DEV-93 — the top bar names every page", () => {
  it("the router gives this guard its pages", () => {
    expect(pages().length).toBeGreaterThan(20);
    expect(pages()).toEqual(
      expect.arrayContaining(["/", "/poradca", "/riadiace-centrum", "/getting-started", "/projects/x/plane-tikety"]),
    );
  });

  it.each(pages())("%s has a name of its own", (path) => {
    expect(nameAt(path)).not.toBe(FALLBACK());
  });

  it("every page without a parameter has a name different from every other — a shared fallback is not a name", () => {
    const fixed = pages().filter((path) => !path.split("/").includes("x"));
    const names = fixed.map(nameAt);
    expect(new Set(names).size).toBe(fixed.length);
  });

  it.each([
    ["/getting-started", "Ako začať"],
    ["/poradca", "Poradca"],
    ["/riadiace-centrum", "Riadiace centrum"],
  ])("%s reads as in the sidebar: %s", (path, name) => {
    topbarAt(path);
    expect(screen.getByText(name)).toBeInTheDocument();
  });
});
