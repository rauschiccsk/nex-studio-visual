/**
 * Unit tests for {@link Sidebar}.
 *
 * NavItems render as <button> (navigate-on-click), not <a>. The UI is Slovak
 * (E4). Tests cover:
 *   1. no standalone English "Users" / "User Sessions" nav button
 *   2. the "Nastavenia" (Settings) nav button is present
 *   3. the brand + version footer renders
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// Mock authStore — Sidebar reads `user` via a selector; null = unauthenticated.
vi.mock("@/store/authStore", () => ({
  useAuthStore: vi.fn(() => null),
}));

import Sidebar from "@/components/layout/Sidebar";

function renderSidebar(route = "/") {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Sidebar />
    </MemoryRouter>,
  );
}

describe("Sidebar", () => {
  it("does not render a standalone 'Users' nav button", () => {
    renderSidebar();
    const buttons = screen.getAllByRole("button");
    expect(buttons.filter((b) => b.textContent?.trim() === "Users")).toHaveLength(0);
  });

  it("does not render a 'User Sessions' nav button", () => {
    renderSidebar();
    const buttons = screen.getAllByRole("button");
    expect(buttons.filter((b) => b.textContent?.trim() === "User Sessions")).toHaveLength(0);
  });

  it("renders the 'Nastavenia' (Settings) nav button", () => {
    renderSidebar();
    expect(screen.getByRole("button", { name: /nastavenia/i })).toBeInTheDocument();
  });

  it("renders the brand + version footer", () => {
    renderSidebar();
    expect(screen.getByText("NEX Studio Visual")).toBeInTheDocument();
    // version is "v{X.Y.Z}" (CI/post-commit) or "vdev" when VITE_APP_VERSION is unset.
    expect(screen.getByText(/^v(\d+\.\d+\.\d+|dev)$/)).toBeInTheDocument();
  });
});
