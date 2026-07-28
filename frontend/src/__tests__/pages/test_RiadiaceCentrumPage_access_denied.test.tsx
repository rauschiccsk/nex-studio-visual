/**
 * RiadiaceCentrumPage — a permission refusal must be SAID, not rendered as a calm empty board.
 *
 * Every pipeline read for a version is owner-or-ri, but the project list shows a Medior every project — so he
 * can pin one that is not his and open Riadiace centrum from the menu. Before the fix the page showed the
 * ordinary board: an empty conversation and a status strip reading "Voľný", while the socket retried a door
 * that never opens. This asserts the explicit no-access state instead: the cause, and a way out that needs no
 * terminal (→ Projekty).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import RiadiaceCentrumPage from "@/pages/RiadiaceCentrumPage";

const { wsMock, navigateMock, contextMock } = vi.hoisted(() => ({
  wsMock: {
    board: null as unknown,
    activity: [] as unknown[],
    reconnecting: false,
    error: null as string | null,
    accessDenied: false,
  },
  navigateMock: vi.fn(),
  contextMock: {
    selectedProject: { slug: "cudzi-projekt", name: "Cudzí projekt" } as { slug: string; name: string } | null,
    selectedVersion: { versionId: "v-1", versionNumber: "1.0.0" } as
      | { versionId: string; versionNumber: string }
      | null,
  },
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: unknown) => unknown) => selector({ user: { role: "ha" } }),
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));
vi.mock("@/hooks/usePipelineWs", () => ({
  usePipelineWs: () => ({ ...wsMock, setBoard: vi.fn() }),
}));
vi.mock("@/services/api/pipeline", () => ({
  relayPipelineMessageApi: vi.fn(),
  postPipelineActionApi: vi.fn(),
}));

// The composer stub marks the ordinary board: if it renders, the page took the normal path.
vi.mock("@/components/riadiace/ConversationComposer", () => ({
  default: () => <button>send</button>,
}));
vi.mock("@/components/riadiace/ConversationThread", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/SpecApprovalBar", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/PhaseBar", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/HonestStatusStrip", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/PlanUlohRail", () => ({ default: () => <div /> }));

describe("RiadiaceCentrumPage — no access to the pinned project", () => {
  beforeEach(() => {
    wsMock.accessDenied = false;
    wsMock.board = null;
    navigateMock.mockReset();
  });

  it("says 'Nemáš prístup k tomuto projektu' instead of rendering the board", () => {
    wsMock.accessDenied = true;
    render(<RiadiaceCentrumPage />);

    expect(screen.getByText(/Nemáš prístup k tomuto projektu/i)).toBeInTheDocument();
    // The affected project is NAMED, so the manager knows which pin caused it.
    expect(screen.getByText("Cudzí projekt")).toBeInTheDocument();
    // No conversation surface at all — a composer here would be an input that can only fail.
    expect(screen.queryByRole("button", { name: "send" })).toBeNull();
  });

  it("offers the way out — → Otvor Projekty (no terminal needed)", () => {
    wsMock.accessDenied = true;
    render(<RiadiaceCentrumPage />);

    fireEvent.click(screen.getByRole("button", { name: /Otvor Projekty/i }));
    expect(navigateMock).toHaveBeenCalledWith("/projects");
  });

  it("renders the ordinary board when access is NOT refused", () => {
    wsMock.accessDenied = false;
    render(<RiadiaceCentrumPage />);

    expect(screen.queryByText(/Nemáš prístup k tomuto projektu/i)).toBeNull();
    expect(screen.getByRole("button", { name: "send" })).toBeInTheDocument();
  });
});
