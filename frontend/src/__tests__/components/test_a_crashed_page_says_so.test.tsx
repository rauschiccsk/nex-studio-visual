/**
 * DEV-192 — a page that crashes while it renders says so, keeps the menu and writes the error to the server.
 *
 * 23.10.2026: the cockpit had no error boundary at all — an exception while rendering left the whole window white and
 * nobody learned why (the Director's „prázdna stránka“ at Poradca had to be told apart from such a crash by a
 * screenshot). Approved by the Director 23.10.2026 („Áno, schvaľujem všetkých 14 bodov“, point 2).
 */
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

vi.mock("@/services/api/clientErrors", () => ({
  reportClientErrorApi: vi.fn().mockResolvedValue(undefined),
}));

// The layout's own parts are stand-ins: what is under test is that the page sits inside the boundary.
vi.mock("@/components/layout/Sidebar", () => ({
  default: () => <nav>ľavé menu (atrapa)</nav>,
}));
vi.mock("@/components/layout/Topbar", () => ({
  default: () => <header>horná lišta</header>,
}));
vi.mock("@/components/PersistentTerminalsLayer", () => ({
  PersistentTerminalsLayer: () => null,
}));
vi.mock("@/store/userLayoutStore", () => ({
  useUserLayoutStore: (sel: (s: unknown) => unknown) => sel({ load: vi.fn() }),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ user: { id: "me" } }),
}));

import PageErrorBoundary from "@/components/layout/PageErrorBoundary";
import { reportClientErrorApi } from "@/services/api/clientErrors";

function Broken(): ReactElement {
  throw new Error("Cannot read properties of undefined (reading 'versions')");
}

function renderAt(element: ReactElement) {
  render(
    <MemoryRouter initialEntries={["/poradca?verzia=v1"]}>
      <nav>ľavé menu</nav>
      <Routes>
        <Route
          path="/poradca"
          element={<PageErrorBoundary>{element}</PageErrorBoundary>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe("a crashed page", () => {
  it("says what happened, offers to reload and keeps the menu", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    renderAt(<Broken />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Stránka narazila na chybu",
    );
    expect(screen.getByRole("button", { name: "Obnoviť" })).toBeInTheDocument();
    expect(screen.getByText("ľavé menu")).toBeInTheDocument();
  });

  it("writes the error to the server with the page it happened on", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    renderAt(<Broken />);

    expect(reportClientErrorApi).toHaveBeenCalledTimes(1);
    expect(vi.mocked(reportClientErrorApi).mock.calls[0]![0]).toMatchObject({
      message: "Cannot read properties of undefined (reading 'versions')",
      path: "/poradca?verzia=v1",
    });
  });

  it("Obnoviť reloads the page", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    renderAt(<Broken />);

    fireEvent.click(screen.getByRole("button", { name: "Obnoviť" }));

    expect(reload).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it("a page that renders is left alone", () => {
    renderAt(<p>Poradca</p>);

    expect(screen.getByText("Poradca")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(reportClientErrorApi).not.toHaveBeenCalled();
  });
});

describe("every page of the cockpit stands inside the boundary", () => {
  it("a page that crashes inside the layout shows the rescue screen with the menu around it", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { default: AppLayout } =
      await import("@/components/layout/AppLayout");
    render(
      <MemoryRouter initialEntries={["/riadiace-centrum"]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/riadiace-centrum" element={<Broken />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Stránka narazila na chybu",
    );
    expect(screen.getByText("ľavé menu (atrapa)")).toBeInTheDocument();
  });
});
