/**
 * CR-V2-019 → spine STEP 1 (Chrbtica) — the Sidebar build surface collapsed to ONE item.
 *
 * The per-charter Designer / Customer / Implementer / Auditor sidebar terminals were removed (CR-NS-039),
 * then the AI Agent terminal + the 4-phase Vývoj board collapsed into ONE "Riadiace centrum" item (spine
 * STEP 1), with a project-scoped "Špecifikácia" item alongside it. This asserts the trimmed sidebar surface
 * plus the AUD-7 invariant (no Auditor nav item ever renders).
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

describe("Sidebar build surface (CR-V2-019 → spine STEP 1)", () => {
  it("shows the single Riadiace centrum item, enabled when a project is pinned", () => {
    renderSidebar();

    // A project is pinned (mocked) → the project-scoped Riadiace centrum item is active.
    const riadiace = screen.getByRole("button", { name: /Riadiace centrum/i });
    expect(riadiace).not.toBeDisabled();

    // It replaces the retired AI Agent tab + Vývoj board — neither renders any more.
    expect(screen.queryByRole("button", { name: /AI Agent/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Vývoj/i })).toBeNull();
  });

  it("shows the project-scoped Dokumenty item, enabled when a project is pinned", () => {
    renderSidebar();

    // The read-only spec shell was renamed "Špecifikácia" → "Dokumenty" (Audit Theme 3: it now lists
    // EVERY doc the AI produced, not just the spec).
    const spec = screen.getByRole("button", { name: /Dokumenty/i });
    expect(spec).not.toBeDisabled();
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

  it("AUD-7: renders NO Auditor / Audítor nav item (verdict lives inside the build surface)", () => {
    renderSidebar();

    // The independent Auditor is intentionally absent from the nav — its verdict surfaces only inside the
    // build surface, never as a standalone destination. A future edit must not re-add it.
    expect(screen.queryByRole("button", { name: /Audítor/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Auditor/i })).toBeNull();
  });
});
