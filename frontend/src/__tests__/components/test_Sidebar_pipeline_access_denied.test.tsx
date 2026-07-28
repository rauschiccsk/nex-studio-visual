/**
 * Sidebar — the Riadiace centrum entry when the pinned project refuses this user.
 *
 * The project LIST is deliberately wider than the pipeline gate: a Medior sees every project and can pin one
 * he does not own, but every pipeline read is owner-or-ri. The entry must then be DISABLED WITH A REASON (the
 * treatment the deploy matrix already uses) instead of opening a screen that can only show an empty board —
 * and the "čaká na Manažéra" attention dot must not fire for a board this user cannot read.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

const { wsMock } = vi.hoisted(() => ({
  wsMock: {
    board: null as { state: { status: string } } | null,
    accessDenied: false,
  },
}));

vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) =>
    sel({ user: { username: "medior", role: "ha" }, logout: vi.fn() }),
}));

vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({
      selectedProject: { slug: "cudzi-projekt", name: "Cudzí projekt" },
      selectedVersion: { versionId: "v-1", versionNumber: "1.0.0" },
      setSelectedProject: vi.fn(),
    }),
}));

vi.mock("@/hooks/usePipelineWs", () => ({
  usePipelineWs: () => wsMock,
}));

import Sidebar from "@/components/layout/Sidebar";

function renderSidebar() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Sidebar />
    </MemoryRouter>,
  );
}

describe("Sidebar — Riadiace centrum on a project this user may not drive", () => {
  beforeEach(() => {
    wsMock.board = null;
    wsMock.accessDenied = false;
  });

  it("disables the entry and says WHY when the pipeline refused this user", () => {
    wsMock.accessDenied = true;
    renderSidebar();

    const riadiace = screen.getByRole("button", { name: /Riadiace centrum/i });
    expect(riadiace).toBeDisabled();
    // Disabled, never hidden — and the tooltip names the cause AND the way out.
    expect(riadiace).toHaveAttribute("title", expect.stringContaining("Nemáš prístup k tomuto projektu"));
    expect(riadiace.getAttribute("title")).toMatch(/Projekt/i);
  });

  it("keeps the entry open (with the pin-a-project tooltip) when there is no refusal", () => {
    wsMock.accessDenied = false;
    renderSidebar();

    const riadiace = screen.getByRole("button", { name: /Riadiace centrum/i });
    expect(riadiace).not.toBeDisabled();
    expect(riadiace.getAttribute("title") ?? "").not.toMatch(/Nemáš prístup/);
  });

  it("never fires the 'čaká na Manažéra' dot for a refused board", () => {
    wsMock.accessDenied = true;
    wsMock.board = { state: { status: "awaiting_manazer" } }; // a stale board from before the refusal
    renderSidebar();

    expect(screen.queryByLabelText("čaká na Manažéra")).toBeNull();
  });
});
