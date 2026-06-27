/**
 * E3(a) (CR-NS-039) → CR-V2-019 — Sidebar AG terminal is a single AI Agent item.
 *
 * Supersedes the CR-NS-014 per-charter gating test: the Designer / Customer /
 * Implementer / Auditor sidebar terminals were removed, so only the single AI
 * Agent NavItem remains (was "AG Koordinátor", renamed in CR-V2-019, design
 * §4.1). The pipeline still runs all work internally — this asserts the trimmed
 * sidebar surface plus the AUD-7 invariant (no Auditor nav item ever renders).
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) =>
    sel({ user: { username: "ri", role: "ri" }, logout: vi.fn() }),
}));

vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({
      selectedProject: { slug: "nex-inbox", name: "NEX Inbox" },
      selectedVersion: null,
      setSelectedProject: vi.fn(),
    }),
}));

import Sidebar from "@/components/layout/Sidebar";

function renderSidebar() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Sidebar />
    </MemoryRouter>,
  );
}

describe("Sidebar AG terminal (E3(a) / CR-NS-039 → CR-V2-019)", () => {
  it("shows the single AI Agent terminal, enabled when a project is pinned", () => {
    renderSidebar();

    // A project is pinned (mocked) → the project-scoped AI Agent item is active.
    const aiAgent = screen.getByRole("button", { name: /AI Agent/i });
    expect(aiAgent).not.toBeDisabled();
  });

  it("no longer renders the Designer / Customer / Implementer / Auditor terminals", () => {
    renderSidebar();

    expect(screen.queryByRole("button", { name: /AG Designer/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /AG Customer/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /AG Implementator/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /AG Auditor/i })).toBeNull();
    // Renamed away from the old vocabulary.
    expect(screen.queryByRole("button", { name: /AG Koordinátor/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Orchestrácia/i })).toBeNull();
  });

  it("AUD-7: renders NO Auditor / Audítor nav item (verdict lives in Vývoj → Verifikácia)", () => {
    renderSidebar();

    // The independent Auditor is intentionally absent from the nav — its verdict
    // surfaces only inside the Vývoj board. A future edit must not re-add it.
    expect(screen.queryByRole("button", { name: /Audítor/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Auditor/i })).toBeNull();
  });

  it("removes the Špecifikácie nav item (spec now lives in Vývoj → Príprava)", () => {
    renderSidebar();

    expect(screen.queryByRole("button", { name: /Špecifikácie/i })).toBeNull();
  });
});
