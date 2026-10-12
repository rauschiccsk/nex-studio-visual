/**
 * DEV-85 — the repository tab carries a red mark while a workflow's newest run on the selected project's main branch is
 * red; green, no project, or no access to the project — no mark, and no question either.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import type { CiMain } from "@/services/api/ciRuns";

const { context, ws, ciMain } = vi.hoisted(() => ({
  context: { selectedProject: { slug: "nex-inbox", name: "NEX Inbox" } as { slug: string; name: string } | null },
  ws: { board: null, accessDenied: false },
  ciMain: vi.fn(),
}));

vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ user: { username: "maria", role: "ri" }, logout: vi.fn() }),
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ ...context, selectedVersion: null, setSelectedProject: vi.fn() }),
}));
vi.mock("@/hooks/usePipelineWs", () => ({ usePipelineWs: () => ws }));
vi.mock("@/services/api/ciRuns", () => ({ getCiMain: ciMain }));

import Sidebar from "@/components/layout/Sidebar";

const RED: CiMain = {
  branch: "main",
  failing: [{ workflow: "CI", number: 168, subject: "feat: disk", failed_step: "lint › ruff", url: "https://x/168" }],
  refresh_seconds: 60,
};

function sidebar() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Sidebar />
    </MemoryRouter>,
  );
}

const mark = () => screen.queryByLabelText("Kontroly hlavnej vetvy zlyhali");

beforeEach(() => {
  context.selectedProject = { slug: "nex-inbox", name: "NEX Inbox" };
  ws.accessDenied = false;
  ciMain.mockReset().mockResolvedValue(RED);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the red mark of the repository tab (DEV-85)", () => {
  it("asks again on the watch's schedule and drops the mark when the answer fails — never an old red", async () => {
    vi.useFakeTimers();
    ciMain.mockResolvedValue({ ...RED, refresh_seconds: 45 });
    sidebar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mark()).toBeInTheDocument();

    ciMain.mockRejectedValue(new Error("offline"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(44_000);
    });
    expect(mark()).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(mark()).not.toBeInTheDocument();
  });

  it("is red on the repository tab while the main branch is red", async () => {
    sidebar();

    await waitFor(() => expect(mark()).toBeInTheDocument());
    expect(ciMain).toHaveBeenCalledWith("nex-inbox");
    expect(mark()).toHaveAttribute("data-tone", "danger");
    expect(screen.getByRole("button", { name: /Repozitár/ })).toContainElement(mark());
  });

  it("is not there when the main branch is green", async () => {
    ciMain.mockResolvedValue({ ...RED, failing: [] });
    sidebar();

    await waitFor(() => expect(ciMain).toHaveBeenCalled());
    expect(mark()).not.toBeInTheDocument();
  });

  it("asks nothing without a project or without access to it", async () => {
    context.selectedProject = null;
    const { unmount } = sidebar();
    unmount();
    context.selectedProject = { slug: "cudzi", name: "Cudzí" };
    ws.accessDenied = true;
    sidebar();

    await new Promise((r) => setTimeout(r, 20));
    expect(ciMain).not.toHaveBeenCalled();
    expect(mark()).not.toBeInTheDocument();
  });
});
