/**
 * Sidebar UserCard subtitle (CR-NS-093) — the subtitle must derive from the
 * logged-in user's role, not be hardcoded. Studio provisions all three roles
 * (ri/ha/shu); the "<Title> · <Code>" style is kept (ri → Manažér, ha → Medior,
 * shu → Junior). CR-V2-004 renamed the operator Director → Manažér.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const mockState = vi.hoisted(() => ({
  user: null as { username: string; role: string } | null,
}));

vi.mock("@/store/authStore", () => ({
  useAuthStore: vi.fn((sel: (s: unknown) => unknown) =>
    sel({ user: mockState.user, logout: vi.fn(), token: null }),
  ),
}));

import Sidebar from "@/components/layout/Sidebar";

function renderSidebar() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Sidebar />
    </MemoryRouter>,
  );
}

describe("Sidebar — UserCard subtitle from role (CR-NS-093)", () => {
  it("ri → 'Manažér'", () => {
    mockState.user = { username: "ri-user", role: "ri" };
    renderSidebar();
    expect(screen.getByText("Manažér")).toBeInTheDocument();
  });

  it("ha → 'Medior'", () => {
    mockState.user = { username: "ha-user", role: "ha" };
    renderSidebar();
    expect(screen.getByText("Medior")).toBeInTheDocument();
  });

  it("shu → 'Junior'", () => {
    mockState.user = { username: "shu-user", role: "shu" };
    renderSidebar();
    expect(screen.getByText("Junior")).toBeInTheDocument();
  });

  it("no user → '—' fallback (name + subtitle both em-dash, no 'undefined')", () => {
    mockState.user = null;
    renderSidebar();
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
    // displayName and subtitle both fall back to "—" when unauthenticated.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });
});
