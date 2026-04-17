/**
 * Navigation tests — Architect links in Sidebar.
 *
 * @vitest-environment jsdom
 *
 * Validates:
 * - Project-level Architect link appears when viewing a project
 * - Module-level Architect link appears when a module is selected
 * - Module-level Architect link is hidden when no module is selected
 * - Both links point to correct routes
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

/* ------------------------------------------------------------------ */
/*  Mocks — lucide-react icons                                        */
/* ------------------------------------------------------------------ */

vi.mock("lucide-react", () => ({
  Brain: (props: Record<string, unknown>) => (
    <svg data-testid="brain-icon" {...props} />
  ),
  Tag: (props: Record<string, unknown>) => (
    <svg data-testid="tag-icon" {...props} />
  ),
}));

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function renderSidebar(initialRoute: string) {
  const { default: Sidebar } = await import("@/components/layout/Sidebar");
  return render(
    <MemoryRouter initialEntries={[initialRoute]}>
      <Routes>
        <Route path="*" element={<Sidebar />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Find the project-level Architect link by its href pattern. */
function findProjectArchitectLink(slug: string) {
  const links = screen.getAllByRole("link");
  return links.find(
    (el) => el.getAttribute("href") === `/projects/${slug}/architect`,
  );
}

/** Find module-level Architect link by text pattern. */
function findModuleArchitectLink(code: string) {
  return screen.queryByText(`Architect (${code})`);
}

beforeEach(() => {
  localStorage.clear();
});

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("Sidebar Architect navigation", () => {
  it("does not show project-level Architect link on dashboard", async () => {
    await renderSidebar("/");
    const link = findProjectArchitectLink("anything");
    expect(link).toBeUndefined();
  });

  it("shows project-level Architect link when viewing a project", async () => {
    await renderSidebar("/projects/my-project/versions");
    const link = findProjectArchitectLink("my-project");
    expect(link).toBeDefined();
    expect(link).toHaveAttribute("href", "/projects/my-project/architect");
  });

  it("does not show module-level Architect link when no module is selected", async () => {
    await renderSidebar("/projects/my-project/versions");
    expect(screen.queryByText(/Architect \(/)).not.toBeInTheDocument();
  });

  it("shows module-level Architect link when a module is selected", async () => {
    await renderSidebar("/projects/my-project/modules/AUTH/architect");
    const moduleLink = findModuleArchitectLink("AUTH");
    expect(moduleLink).toBeInTheDocument();
    const anchor = moduleLink!.closest("a");
    expect(anchor).toHaveAttribute(
      "href",
      "/projects/my-project/modules/AUTH/architect",
    );
  });

  it("shows both project and module Architect links when module selected", async () => {
    await renderSidebar("/projects/my-project/modules/CORE/architect");
    // Project-level link
    const projectLink = findProjectArchitectLink("my-project");
    expect(projectLink).toBeDefined();
    // Module-level link
    const moduleLink = findModuleArchitectLink("CORE");
    expect(moduleLink).toBeInTheDocument();
  });

  it("project-level Architect link points to correct route from module context", async () => {
    await renderSidebar("/projects/my-project/modules/AUTH/architect");
    const projectLink = findProjectArchitectLink("my-project");
    expect(projectLink).toBeDefined();
    expect(projectLink).toHaveAttribute(
      "href",
      "/projects/my-project/architect",
    );
  });
});
