/**
 * DEV-318 — Nápady sits ABOVE Projekty and opens without a pinned project.
 *
 * Director 06.11.2026: „Poradca nie je dostupný pokiaľ nemám pripojený konkrétny projekt. Robiť nový projekt pod iným
 * cudzím projektom, je nelogické.“ — „Podľa mojej logiky "Návrhy" má byť nie pod ale nad "Projekty".“ — and on the name:
 * „Súhlasím, nazveme to Nápady“.
 */

import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ user: { username: "ri", role: "ri" }, logout: vi.fn() }),
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ selectedProject: null, selectedVersion: null, setSelectedProject: vi.fn() }),
}));

import Sidebar from "@/components/layout/Sidebar";

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

describe("Sidebar — Nápady (DEV-318)", () => {
  it("stands right above Projekty and opens Nápady with no project pinned", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar />
        <Routes>
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>,
    );

    const tabs = screen.getAllByRole("button").map((b) => b.textContent ?? "");
    const projects = tabs.findIndex((t) => t.includes("Projekty"));
    expect(tabs[projects - 1]).toContain("Nápady");
    const ideas = screen.getByRole("button", { name: /Nápady/ });
    expect(ideas).toBeEnabled();
    fireEvent.click(ideas);
    expect(screen.getByTestId("where")).toHaveTextContent("/napady");
  });
});
