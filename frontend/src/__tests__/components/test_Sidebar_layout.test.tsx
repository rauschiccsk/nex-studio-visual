/**
 * DEV-81 — the sidebar is dense: 28 px tabs, a line instead of the heading above „Nastavenia“.
 * DEV-82 — its width and its collapsed state are the person's own, kept on the server: the edge is a handle (keys,
 * double click back to the default), the bounds hold, and every change is sent to the server.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

vi.mock("@/store/authStore", () => ({
  useAuthStore: vi.fn((sel: (s: unknown) => unknown) =>
    sel({ user: { username: "maria", role: "ri" }, logout: vi.fn(), token: null }),
  ),
}));
vi.mock("@/services/api/userLayout", () => ({ getUserLayout: vi.fn(), saveUserLayout: vi.fn() }));

import Sidebar from "@/components/layout/Sidebar";
import { saveUserLayout } from "@/services/api/userLayout";
import { useUserLayoutStore } from "@/store/userLayoutStore";

const LIMITS = {
  sidebar_width: { min: 180, max: 360, default: 224 },
  plan_width: { min: 280, max: 640, default: 320 },
};
const layout = (over: object = {}) => ({
  sidebar_width: null,
  sidebar_collapsed: false,
  plan_width: null,
  limits: LIMITS,
  ...over,
});

function renderSidebar() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Sidebar />
    </MemoryRouter>,
  );
}

const aside = () => document.querySelector("aside") as HTMLElement;
const handle = () => screen.getByRole("separator", { name: "Šírka bočného panela" });

beforeEach(() => {
  vi.mocked(saveUserLayout)
    .mockReset()
    .mockImplementation(async (changes) => layout({ ...useUserLayoutStore.getState().layout, ...changes }));
  useUserLayoutStore.setState({ layout: layout() });
});

describe("dense sidebar (DEV-81)", () => {
  it("has 28 px tabs and a line, not a heading, above Nastavenia", () => {
    renderSidebar();

    expect(screen.getByRole("button", { name: "Prehľad" }).className).toMatch(/\bh-7\b/);
    expect(screen.getAllByText("Nastavenia")).toHaveLength(1);
    expect(screen.getByRole("separator", { name: "" }).tagName).toBe("HR");
  });
});

describe("the sidebar's width and collapse are the person's own (DEV-82)", () => {
  it("shows the width kept on the server", () => {
    useUserLayoutStore.setState({ layout: layout({ sidebar_width: 300 }) });
    renderSidebar();

    expect(aside().style.width).toBe("300px");
    expect(handle()).toHaveAttribute("aria-valuenow", "300");
  });

  it("moves the edge with the keys within the bounds and sends every change", async () => {
    renderSidebar();

    fireEvent.keyDown(handle(), { key: "ArrowRight" });
    await waitFor(() => expect(saveUserLayout).toHaveBeenLastCalledWith({ sidebar_width: 240 }));
    expect(aside().style.width).toBe("240px");

    fireEvent.keyDown(handle(), { key: "End" });
    await waitFor(() => expect(saveUserLayout).toHaveBeenLastCalledWith({ sidebar_width: 360 }));
    fireEvent.keyDown(handle(), { key: "ArrowRight" }); // already the widest
    fireEvent.keyDown(handle(), { key: "Home" });
    await waitFor(() => expect(saveUserLayout).toHaveBeenLastCalledWith({ sidebar_width: 180 }));
    expect(saveUserLayout).toHaveBeenCalledTimes(3);
  });

  it("a double click returns to the default, kept as none chosen", async () => {
    useUserLayoutStore.setState({ layout: layout({ sidebar_width: 300 }) });
    renderSidebar();

    fireEvent.doubleClick(handle());

    await waitFor(() => expect(saveUserLayout).toHaveBeenCalledWith({ sidebar_width: null }));
  });

  it("a drag follows the pointer and is sent once, when it ends", async () => {
    renderSidebar();
    const edge = handle();
    edge.setPointerCapture = vi.fn();
    edge.hasPointerCapture = vi.fn(() => true);
    edge.releasePointerCapture = vi.fn();

    fireEvent.pointerDown(edge, { button: 0, clientX: 224, pointerId: 1 });
    fireEvent.pointerMove(edge, { clientX: 250, pointerId: 1 });
    fireEvent.pointerMove(edge, { clientX: 500, pointerId: 1 });
    expect(aside().style.width).toBe("360px"); // never past the widest
    expect(saveUserLayout).not.toHaveBeenCalled();
    fireEvent.pointerUp(edge, { clientX: 500, pointerId: 1 });

    await waitFor(() => expect(saveUserLayout).toHaveBeenCalledWith({ sidebar_width: 360 }));
    expect(saveUserLayout).toHaveBeenCalledTimes(1);
  });

  it("stays collapsed as kept, and collapsing is sent to the server", async () => {
    useUserLayoutStore.setState({ layout: layout({ sidebar_collapsed: true }) });
    renderSidebar();
    expect(aside().style.width).toBe("3.5rem");
    expect(screen.queryByRole("separator", { name: "Šírka bočného panela" })).toBeNull();

    fireEvent.click(screen.getByTitle("Rozšíriť"));

    await waitFor(() => expect(saveUserLayout).toHaveBeenCalledWith({ sidebar_collapsed: false }));
    expect(screen.getByTitle("Zúžiť")).toBeInTheDocument();
  });

  it("a change the server refuses does not stay on the screen", async () => {
    vi.mocked(saveUserLayout).mockRejectedValue(new Error("422"));
    renderSidebar();

    fireEvent.keyDown(handle(), { key: "ArrowRight" });

    await waitFor(() => expect(saveUserLayout).toHaveBeenCalled());
    await waitFor(() => expect(handle()).toHaveAttribute("aria-valuenow", "224"));
  });

  it("before the layout arrives the sidebar keeps its width and has no handle", () => {
    useUserLayoutStore.setState({ layout: null });
    renderSidebar();

    expect(aside().style.width).toBe("14rem");
    expect(screen.queryByRole("separator", { name: "Šírka bočného panela" })).toBeNull();
  });
});
