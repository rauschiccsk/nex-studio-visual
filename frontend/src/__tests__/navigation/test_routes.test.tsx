/**
 * Route resolution tests for Versions pages.
 *
 * Verifies that:
 *   1. ``/projects/:slug/versions`` renders the VersionsPage component
 *   2. ``/projects/:slug/versions/:vid`` renders the VersionDetailPage component
 *   3. Sidebar shows the "Versions" navigation link inside a project context
 *
 * Uses MemoryRouter to drive route matching without a real browser.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

/* ------------------------------------------------------------------ */
/*  Lightweight stub pages — we only care about route resolution, not  */
/*  page internals.                                                    */
/* ------------------------------------------------------------------ */

function StubVersionsPage() {
  return <div data-testid="versions-page">VersionsPage</div>;
}

function StubVersionDetailPage() {
  return <div data-testid="version-detail-page">VersionDetailPage</div>;
}

function StubFallback() {
  return <div data-testid="fallback">Not Found</div>;
}

/* ------------------------------------------------------------------ */
/*  Mocks — lucide-react is an optional dep; stub it out so we don't  */
/*  need the full icon library in the test environment.                */
/* ------------------------------------------------------------------ */

vi.mock("lucide-react", () => ({
  Brain: (props: Record<string, unknown>) => <svg data-testid="brain-icon" {...props} />,
  Settings: (props: Record<string, unknown>) => <svg data-testid="settings-icon" {...props} />,
  Tag: (props: Record<string, unknown>) => <svg data-testid="tag-icon" {...props} />,
}));

/* ------------------------------------------------------------------ */
/*  Helper                                                             */
/* ------------------------------------------------------------------ */

function renderWithRouter(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route
          path="projects/:slug/versions"
          element={<StubVersionsPage />}
        />
        <Route
          path="projects/:slug/versions/:vid"
          element={<StubVersionDetailPage />}
        />
        <Route path="*" element={<StubFallback />} />
      </Routes>
    </MemoryRouter>,
  );
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("Versions route resolution", () => {
  it("renders VersionsPage at /projects/:slug/versions", () => {
    renderWithRouter("/projects/my-project/versions");

    expect(screen.getByTestId("versions-page")).toBeInTheDocument();
    expect(screen.queryByTestId("version-detail-page")).toBeNull();
    expect(screen.queryByTestId("fallback")).toBeNull();
  });

  it("renders VersionDetailPage at /projects/:slug/versions/:vid", () => {
    renderWithRouter("/projects/my-project/versions/v-123");

    expect(screen.getByTestId("version-detail-page")).toBeInTheDocument();
    expect(screen.queryByTestId("versions-page")).toBeNull();
    expect(screen.queryByTestId("fallback")).toBeNull();
  });

  it("renders fallback for unmatched routes", () => {
    renderWithRouter("/projects/my-project/unknown");

    expect(screen.getByTestId("fallback")).toBeInTheDocument();
    expect(screen.queryByTestId("versions-page")).toBeNull();
    expect(screen.queryByTestId("version-detail-page")).toBeNull();
  });
});

describe("Sidebar Versions link", () => {
  it("renders the 'Verzie' nav button when inside a project context", async () => {
    // Import Sidebar dynamically so the lucide-react mock is in place
    const { default: Sidebar } = await import(
      "@/components/layout/Sidebar"
    );

    render(
      <MemoryRouter initialEntries={["/projects/test-proj/versions"]}>
        <Routes>
          <Route path="projects/:slug/*" element={<Sidebar />} />
        </Routes>
      </MemoryRouter>,
    );

    // NavItems render as <button> (navigate-on-click), and the E4 label is Slovak.
    expect(screen.getByRole("button", { name: /verzie/i })).toBeInTheDocument();
  });

  it("does NOT render the Versions link outside a project context", async () => {
    const { default: Sidebar } = await import(
      "@/components/layout/Sidebar"
    );

    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <Routes>
          <Route path="projects" element={<Sidebar />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.queryByRole("link", { name: /versions/i })).toBeNull();
  });
});
