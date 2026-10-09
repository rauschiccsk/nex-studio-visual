/**
 * DEV-21 — the cockpit offers „Nainštalovať aplikáciu" on its start page (Prehľad), as NEX Manager offers it on its
 * launcher. Director 08.10.2026: „Ako by som mohol urobiť aby nepúšťať NEX Studio Visual z Chrome ale ako PWA?"
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/services/api/projects", () => ({ listProjectsApi: vi.fn(() => Promise.resolve({ items: [] })) }));

import DashboardPage from "@/pages/DashboardPage";

describe("DEV-21 — the start page offers the installation", () => {
  it("shows the install button", async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "Nainštalovať aplikáciu" })).toBeInTheDocument();
  });
});
