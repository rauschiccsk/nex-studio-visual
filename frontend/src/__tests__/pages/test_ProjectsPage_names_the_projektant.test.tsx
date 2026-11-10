/**
 * DEV-343 — the Projekty page opens Nápady with the Projektant, not with „Poradca“.
 *
 * Director 10.11.2026: the agent of Nápady was called Poradca like the project's, „pojem Poradca je chaotická“. Both
 * buttons that open Nápady — above the list and under an empty one — name the Projektant.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({ listProjectsApi: vi.fn() }));
vi.mock("@/services/api/projects", () => api);
vi.mock("@/services/api/versions", () => ({ listVersions: vi.fn().mockResolvedValue([]) }));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ selectedProject: null, setSelectedProject: vi.fn(), setSelectedVersion: vi.fn() }),
}));
vi.mock("@/components/project/AdoptProjectDialog", () => ({ default: () => null }));

import ProjectsPage from "@/pages/ProjectsPage";

describe("Projekty open Nápady with the Projektant (DEV-343)", () => {
  it("both ways into Nápady say Projektant and nothing says Poradca", async () => {
    api.listProjectsApi.mockResolvedValue({ items: [], total: 0 });
    render(
      <MemoryRouter>
        <ProjectsPage />
      </MemoryRouter>,
    );

    // The second one stands under the empty list — once the projects have been read.
    await screen.findByText("Žiadne projekty");
    expect(screen.getAllByRole("button", { name: /Navrhnúť s Projektantom/ })).toHaveLength(2);
    expect((document.body.textContent ?? "").match(/Poradc/g)).toBeNull();
  });
});
