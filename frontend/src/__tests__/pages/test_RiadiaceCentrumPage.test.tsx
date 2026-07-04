/**
 * RiadiaceCentrumPage — the conversation COLD-START send handler (spine STEP 1 HOT-FIX).
 *
 * A freshly-created version has NO pipeline yet (``board.state`` is null), so nothing has ever called
 * ``start`` — a plain relay would raise "Pipeline not started". The page's send handler therefore branches:
 *   - null state    → the FIRST message STARTS the conversation via ``postPipelineActionApi(start,
 *                      {mode: 'conversation', directive: text})`` and adopts the returned board.
 *   - existing state → the message goes through the single-writer relay (Model B) unchanged.
 *
 * The heavy children are stubbed; the ConversationComposer stub exposes the page's ``onRelay`` prop through a
 * button so a send with a known text can be driven and the resulting API call asserted.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import RiadiaceCentrumPage from "@/pages/RiadiaceCentrumPage";
import { relayPipelineMessageApi, postPipelineActionApi } from "@/services/api/pipeline";
import type { PipelineBoard } from "@/services/api/pipeline";

// ── Hoisted mocks (referenced inside vi.mock factories, which hoist above module-scope consts) ────────────

const { wsMock, setBoardMock, authMock, contextMock } = vi.hoisted(() => ({
  wsMock: {
    board: null as import("@/services/api/pipeline").PipelineBoard | null,
    activity: [] as unknown[],
    reconnecting: false,
    error: null as string | null,
  },
  setBoardMock: vi.fn(),
  authMock: { user: { role: "ri" } as { role: string } | null },
  contextMock: {
    selectedProject: { slug: "demo", name: "Demo" } as { slug: string; name: string } | null,
    selectedVersion: { versionId: "v-1", versionNumber: "2.0.0" } as
      | { versionId: string; versionNumber: string }
      | null,
  },
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: typeof authMock) => unknown) => selector(authMock),
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));
vi.mock("@/hooks/usePipelineWs", () => ({
  usePipelineWs: () => ({ ...wsMock, setBoard: setBoardMock }),
}));
vi.mock("@/services/api/pipeline", () => ({
  relayPipelineMessageApi: vi.fn(),
  postPipelineActionApi: vi.fn(),
}));

// The composer stub renders a button that fires the page's onRelay prop with a known text.
const SEND_TEXT = "Ahoj, poďme spolu navrhnúť appku.";
vi.mock("@/components/riadiace/ConversationComposer", () => ({
  default: ({ onRelay }: { onRelay: (t: string) => Promise<{ deferred: boolean }> }) => (
    <button onClick={() => void onRelay(SEND_TEXT)}>send</button>
  ),
}));
vi.mock("@/components/riadiace/ConversationThread", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/SpecApprovalBar", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/PhaseBar", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/HonestStatusStrip", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/PlanUlohRail", () => ({ default: () => <div /> }));

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("RiadiaceCentrumPage — conversation cold-start send handler (STEP 1)", () => {
  beforeEach(() => {
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(relayPipelineMessageApi).mockReset();
    setBoardMock.mockReset();
    wsMock.board = null;
  });

  it("null pipeline state → the FIRST message STARTS the conversation (start, mode=conversation, the text)", async () => {
    const nextBoard = { state: { status: "agent_working" }, recent_messages: [] } as unknown as PipelineBoard;
    vi.mocked(postPipelineActionApi).mockResolvedValue(nextBoard);
    wsMock.board = null; // no pipeline yet — the cold-start precondition

    render(<RiadiaceCentrumPage />);
    fireEvent.click(screen.getByRole("button", { name: "send" }));

    await waitFor(() =>
      expect(postPipelineActionApi).toHaveBeenCalledWith("v-1", {
        action: "start",
        payload: { mode: "conversation", directive: SEND_TEXT },
      }),
    );
    // The returned board is adopted; the relay is NEVER used on a cold-start.
    await waitFor(() => expect(setBoardMock).toHaveBeenCalledWith(nextBoard));
    expect(relayPipelineMessageApi).not.toHaveBeenCalled();
  });

  it("existing pipeline state → the message goes through the single-writer relay (never start)", async () => {
    vi.mocked(relayPipelineMessageApi).mockResolvedValue({ deferred: false, board: {} as PipelineBoard });
    wsMock.board = { state: { status: "awaiting_manazer" }, recent_messages: [] } as unknown as PipelineBoard;

    render(<RiadiaceCentrumPage />);
    fireEvent.click(screen.getByRole("button", { name: "send" }));

    await waitFor(() => expect(relayPipelineMessageApi).toHaveBeenCalledWith("v-1", SEND_TEXT));
    // A live pipeline must NOT re-run start.
    expect(postPipelineActionApi).not.toHaveBeenCalled();
  });
});
