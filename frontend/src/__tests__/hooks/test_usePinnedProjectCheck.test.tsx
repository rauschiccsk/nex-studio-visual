/**
 * DEV-262 — a pinned project the person no longer has is unpinned, and the sidebar says why.
 * 30.10.2026 a project moved to another user by the admin stayed pinned for its former owner.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import { usePinnedProjectCheck } from "@/hooks/usePinnedProjectCheck";
import { listProjectsApi } from "@/services/api/projects";
import { useActiveContextStore } from "@/store/activeContextStore";
import { useAuthStore } from "@/store/authStore";

vi.mock("@/services/api/projects", () => ({ listProjectsApi: vi.fn() }));
vi.mock("@/hooks/usePipelineWs", () => ({ usePipelineWs: () => ({ board: null, accessDenied: false }) }));

const PINNED = { slug: "nex-knowledge", name: "NEX Knowledge" };
const page = (total: number) => ({ items: [], total, skip: 0, limit: 1 });

function signedIn() {
  useAuthStore.setState({ user: { id: "u-1", username: "tibor", role: "shu" } as never });
}

describe("usePinnedProjectCheck (DEV-262)", () => {
  beforeEach(() => {
    vi.mocked(listProjectsApi).mockReset();
    useActiveContextStore.setState({
      selectedProject: PINNED,
      selectedVersion: { versionId: "v-1", versionNumber: "0.2.0" },
      unpinnedName: null,
    });
    signedIn();
  });

  it("asks for the pinned project by its slug and unpins it when the person no longer has it", async () => {
    vi.mocked(listProjectsApi).mockResolvedValue(page(0));

    renderHook(() => usePinnedProjectCheck());

    await waitFor(() => expect(useActiveContextStore.getState().selectedProject).toBeNull());
    expect(listProjectsApi).toHaveBeenCalledWith({ slug: "nex-knowledge", limit: 1 });
    expect(useActiveContextStore.getState().selectedVersion).toBeNull();
    expect(useActiveContextStore.getState().unpinnedName).toBe("NEX Knowledge");
  });

  it("keeps a project the person still has", async () => {
    vi.mocked(listProjectsApi).mockResolvedValue(page(1));

    renderHook(() => usePinnedProjectCheck());

    await waitFor(() => expect(listProjectsApi).toHaveBeenCalledTimes(1));
    await act(async () => undefined);
    expect(useActiveContextStore.getState().selectedProject).toEqual(PINNED);
    expect(useActiveContextStore.getState().unpinnedName).toBeNull();
  });

  it("unpins nothing when the question fails — an outage is not „no longer yours“", async () => {
    vi.mocked(listProjectsApi).mockRejectedValue(new Error("network"));

    renderHook(() => usePinnedProjectCheck());

    await waitFor(() => expect(listProjectsApi).toHaveBeenCalledTimes(1));
    await act(async () => undefined);
    expect(useActiveContextStore.getState().selectedProject).toEqual(PINNED);
  });

  it("asks again when the window comes back into view", async () => {
    vi.mocked(listProjectsApi).mockResolvedValueOnce(page(1)).mockResolvedValueOnce(page(0));
    renderHook(() => usePinnedProjectCheck());
    await waitFor(() => expect(listProjectsApi).toHaveBeenCalledTimes(1));

    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await waitFor(() => expect(useActiveContextStore.getState().selectedProject).toBeNull());
    expect(listProjectsApi).toHaveBeenCalledTimes(2);
  });

  it("asks nothing without a signed-in person or a pin", async () => {
    useAuthStore.setState({ user: null });
    renderHook(() => usePinnedProjectCheck());
    useActiveContextStore.setState({ selectedProject: null });
    signedIn();
    renderHook(() => usePinnedProjectCheck());
    await act(async () => undefined);
    expect(listProjectsApi).not.toHaveBeenCalled();
  });
});

describe("Sidebar says why the project is gone (DEV-262)", () => {
  beforeEach(() => {
    vi.mocked(listProjectsApi).mockReset();
    signedIn();
  });

  it("shows the sentence once the project is unpinned and lets the person close it", async () => {
    vi.mocked(listProjectsApi).mockResolvedValue(page(0));
    useActiveContextStore.setState({ selectedProject: PINNED, selectedVersion: null, unpinnedName: null });
    const { default: Sidebar } = await import("@/components/layout/Sidebar");

    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar />
      </MemoryRouter>,
    );

    expect(await screen.findByText("„NEX Knowledge“ už nemáš k dispozícii — výber projektu som zrušil.")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("Zavrieť"));
    expect(screen.queryByText(/už nemáš k dispozícii/)).not.toBeInTheDocument();
    expect(useActiveContextStore.getState().unpinnedName).toBeNull();
  });
});

describe("the sentence belongs to this visit (DEV-262)", () => {
  it("is not written into the browser with the pin", () => {
    const partialize = useActiveContextStore.persist.getOptions().partialize!;
    const saved = partialize({ ...useActiveContextStore.getState(), unpinnedName: "NEX Knowledge" });
    expect(saved).not.toHaveProperty("unpinnedName");
  });
});
