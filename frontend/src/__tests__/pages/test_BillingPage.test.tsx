/**
 * DEV-73 — the billing page of a project: the statements of all its versions, under the project named in the address.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ selectedProject: { slug: "nex-inbox", name: "NEX Inbox" }, selectedVersion: null }),
}));
vi.mock("@/components/naklady/BillingTab", () => ({
  default: ({ slug }: { slug: string }) => <div>súpisy projektu {slug}</div>,
}));

import BillingPage from "@/pages/BillingPage";

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="projects/:slug/billing" element={<BillingPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("BillingPage (DEV-73)", () => {
  it("shows the billing of the project in the address under its name", () => {
    renderAt("/projects/nex-inbox/billing");

    expect(screen.getByRole("heading", { name: "Fakturácia — NEX Inbox" })).toBeInTheDocument();
    expect(screen.getByText("súpisy projektu nex-inbox")).toBeInTheDocument();
  });

  it("names a project that is not the selected one by its slug", () => {
    renderAt("/projects/career-asistent/billing");

    expect(screen.getByRole("heading", { name: "Fakturácia — career-asistent" })).toBeInTheDocument();
    expect(screen.getByText("súpisy projektu career-asistent")).toBeInTheDocument();
  });
});
