/**
 * Sidebar E6 presence toggle (CR-NS-038) — "🟢 Pri počítači / 🌙 Preč".
 * DEV-81: one line with the user card — the state is the icon and its color, the sentence its accessible name.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// authStore returns a Director (role "ri") so the toggle renders; selector-applied fake state.
vi.mock("@/store/authStore", () => ({
  useAuthStore: vi.fn((sel: (s: unknown) => unknown) =>
    sel({ user: { username: "zoltan", role: "ri" }, logout: vi.fn(), token: null }),
  ),
}));
vi.mock("@/services/api/userLayout", () => ({
  getUserLayout: vi.fn(),
  saveUserLayout: vi.fn(async (changes: object) => ({ ...LAYOUT, ...changes })),
}));

import Sidebar from "@/components/layout/Sidebar";
import { usePresenceStore } from "@/store/usePresenceStore";
import { useUserLayoutStore } from "@/store/userLayoutStore";

const LAYOUT = {
  sidebar_width: null,
  sidebar_collapsed: false,
  plan_width: null,
  limits: {
    sidebar_width: { min: 180, max: 360, default: 224 },
    plan_width: { min: 280, max: 640, default: 320 },
  },
};

function renderSidebar() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Sidebar />
    </MemoryRouter>,
  );
}

const AT_COMPUTER = "Pri počítači — prepnúť na Preč";
const AWAY = "Preč — prepnúť na Pri počítači";

describe("Sidebar — E6 presence toggle (CR-NS-038)", () => {
  beforeEach(() => {
    usePresenceStore.setState({ isAway: false });
    useUserLayoutStore.setState({ layout: LAYOUT });
  });

  it("renders the presence toggle beside the user card, at-computer by default", () => {
    renderSidebar();
    const toggle = screen.getByRole("button", { name: AT_COMPUTER });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(toggle).toHaveTextContent("🟢");
    expect(screen.queryByRole("button", { name: AWAY })).not.toBeInTheDocument();
  });

  it("clicking toggles to away and back, driving usePresenceStore", () => {
    renderSidebar();
    fireEvent.click(screen.getByRole("button", { name: AT_COMPUTER }));
    expect(usePresenceStore.getState().isAway).toBe(true);
    expect(screen.getByRole("button", { name: AWAY })).toHaveTextContent("🌙");

    fireEvent.click(screen.getByRole("button", { name: AWAY }));
    expect(usePresenceStore.getState().isAway).toBe(false);
    expect(screen.getByRole("button", { name: AT_COMPUTER })).toBeInTheDocument();
  });

  it("collapsed sidebar still has the toggle", async () => {
    renderSidebar();
    fireEvent.click(screen.getByTitle("Zúžiť")); // collapse
    await waitFor(() => expect(screen.getByTitle("Rozšíriť")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: AT_COMPUTER })).toHaveTextContent("🟢");
  });
});
