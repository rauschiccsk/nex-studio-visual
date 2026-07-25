/**
 * Sidebar E6 presence toggle (CR-NS-038; broadened v4.0.48) — EVERY operator sees it, not just the
 * admin. A non-Director (role "ha"/"shu") who owns + operates their own projects needs the same
 * presence signal (the backend notify is already per-user — pings the away user's OWN chat_id). This
 * pins the broadened gate: was `role === "ri"` (a leftover from before the ownership model).
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// authStore returns a NON-Director user (role "ha").
vi.mock("@/store/authStore", () => ({
  useAuthStore: vi.fn((sel: (s: unknown) => unknown) =>
    sel({ user: { username: "ha-user", role: "ha" }, logout: vi.fn(), token: null }),
  ),
}));

import Sidebar from "@/components/layout/Sidebar";

describe("Sidebar — E6 presence toggle (CR-NS-038; broadened v4.0.48)", () => {
  it("renders the presence toggle for a non-Director operator", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar />
      </MemoryRouter>,
    );
    // The toggle button is present — its title carries the "Pri počítači" hint regardless of collapse
    // state (the label text is hidden when the sidebar is collapsed, the title is not).
    expect(screen.getByTitle(/Pri počítači/)).toBeInTheDocument();
  });
});
