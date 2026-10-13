/**
 * DEV-73 — billing is a sidebar tab of its own, right below the costs tab, not a view inside the costs page.
 *
 * On 13.10.2026 the Director asked for it as a tab of its own right below the costs tab. Like the costs tab it belongs
 * to the selected project, so without one it is disabled rather than sending anywhere else.
 */

import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

const { context } = vi.hoisted(() => ({
  context: { selectedProject: null as null | { slug: string; name: string } },
}));

vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ user: { username: "ri", role: "ri" }, logout: vi.fn() }),
}));

vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ selectedProject: context.selectedProject, selectedVersion: null, setSelectedProject: vi.fn() }),
}));

import Sidebar from "@/components/layout/Sidebar";

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function renderSidebar() {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Sidebar />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Sidebar billing link (DEV-73)", () => {
  it("sits right below the costs tab and opens the billing of the selected project", () => {
    context.selectedProject = { slug: "nex-inbox", name: "NEX Inbox" };
    renderSidebar();

    const tabs = screen.getAllByRole("button").map((b) => b.textContent ?? "");
    const costs = tabs.findIndex((t) => t.includes("Náklady"));
    expect(tabs[costs + 1]).toContain("Fakturácia");

    fireEvent.click(screen.getByRole("button", { name: /Fakturácia/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/projects/nex-inbox/billing");
  });

  it("is disabled when no project is selected, like the costs tab", () => {
    context.selectedProject = null;
    renderSidebar();

    expect(screen.getByRole("button", { name: /Fakturácia/ })).toBeDisabled();
  });
});
