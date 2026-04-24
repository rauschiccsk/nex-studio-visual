/**
 * Unit tests for {@link Sidebar}.
 *
 * Tests cover:
 *   1. "Users" top-level link is NOT rendered
 *   2. "User Sessions" top-level link is NOT rendered
 *   3. "Settings" link appears at bottom of sidebar
 *   4. Version text "NEX Studio v{version}" is rendered
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

/* ------------------------------------------------------------------ */
/*  Mocks                                                              */
/* ------------------------------------------------------------------ */

// Mock authStore — Sidebar doesn't use it directly but some imports may
vi.mock("@/store/authStore", () => ({
  useAuthStore: vi.fn(() => null),
}));

/* ------------------------------------------------------------------ */
/*  Import under test                                                  */
/* ------------------------------------------------------------------ */

import Sidebar from "@/components/layout/Sidebar";

/** Helper to render Sidebar inside a router context. */
function renderSidebar(route = "/") {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Sidebar />
    </MemoryRouter>,
  );
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("Sidebar", () => {
  it("does not render a top-level 'Users' link", () => {
    renderSidebar();
    // The sidebar should not have a standalone "Users" link in the
    // primary navigation.  The admin "Access" group may still exist
    // but must not contain "Users".
    const links = screen.getAllByRole("link");
    const usersLinks = links.filter(
      (link) => link.textContent?.trim() === "Users",
    );
    expect(usersLinks).toHaveLength(0);
  });

  it("does not render a 'User Sessions' link", () => {
    renderSidebar();
    const links = screen.getAllByRole("link");
    const sessionLinks = links.filter(
      (link) => link.textContent?.trim() === "User Sessions",
    );
    expect(sessionLinks).toHaveLength(0);
  });

  it("renders a 'Settings' link at the bottom of the sidebar", () => {
    renderSidebar();
    const settingsLink = screen.getByRole("link", { name: /settings/i });
    expect(settingsLink).toBeInTheDocument();
    expect(settingsLink).toHaveAttribute("href", "/settings");
  });

  it("renders the version text", () => {
    renderSidebar();
    const versionEl = screen.getByTestId("version-text");
    expect(versionEl).toBeInTheDocument();
    // Version is "X.Y.Z" (CI uses ``0.1.<run_number>``, the local
    // post-commit hook uses ``0.1.<commit_count>``), and "dev" when
    // VITE_APP_VERSION is unset entirely.
    expect(versionEl.textContent).toMatch(/^NEX Studio v(\d+\.\d+\.\d+|dev)$/);
  });
});
