/**
 * ControlCenterPage — the conversation COLD-START send handler (spine STEP 1 HOT-FIX).
 *
 * A freshly-created version has NO pipeline yet (``board.state`` is null), so nothing has ever called
 * ``start`` — a plain relay would raise "Pipeline not started". The page's send handler therefore branches:
 *   - null state    → DEV-351: a message never starts the version (it used to start it as a conversation,
 *                      without the five phases); „Spustiť tvorbu špecifikácie“ (StartBuildBar) does.
 *   - existing state → the message goes through the single-writer relay (Model B) unchanged.
 *
 * The heavy children are stubbed; the ConversationComposer stub exposes the page's ``onRelay`` prop through a
 * button so a send with a known text can be driven and the resulting API call asserted.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import ControlCenterPage from "@/pages/ControlCenterPage";
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
const refused = vi.hoisted(() => ({ message: "" }));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  // DEV-84: the page reads `?uloha=` — rendered without a router here, so the address is empty.
  return { ...actual, useNavigate: () => vi.fn(), useSearchParams: () => [new URLSearchParams(), vi.fn()] };
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
  // ICCINT-129: stránka sa pýta na stav zostavenia. Atrapa, ktorá dopyt nepozná, nie je „o jeden
  // menej" — volanie `undefined` vyhodí chybu SYNCHRÓNNE a zhodí celú obrazovku.
  getCiStatusApi: vi.fn().mockResolvedValue({ stav: "unknown", detail: "—", sha: null }),
}));

// The composer stub renders a button that fires the page's onRelay prop with a known text.
const SEND_TEXT = "Ahoj, poďme spolu navrhnúť aplikáciu.";
vi.mock("@/components/controlCenter/ConversationComposer", () => ({
  default: ({ onRelay }: { onRelay: (t: string) => Promise<{ deferred: boolean }> }) => (
    <button onClick={() => void onRelay(SEND_TEXT).catch((e: Error) => (refused.message = e.message))}>send</button>
  ),
}));
vi.mock("@/components/controlCenter/ConversationThread", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/SpecApprovalBar", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/PhaseBar", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/HonestStatusStrip", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/TaskPlanRail", () => ({ default: () => <div /> }));

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("ControlCenterPage — conversation cold-start send handler (STEP 1)", () => {
  beforeEach(() => {
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(relayPipelineMessageApi).mockReset();
    setBoardMock.mockReset();
    wsMock.board = null;
    refused.message = "";
  });

  it("null pipeline state → a message never starts the version; it is refused with where to start (DEV-351)", async () => {
    wsMock.board = null; // no pipeline yet

    render(<ControlCenterPage />);
    fireEvent.click(screen.getByRole("button", { name: "send" }));

    await waitFor(() =>
      expect(refused.message).toBe("Verzia ešte nebeží — spusti tvorbu špecifikácie tlačidlom vyššie."),
    );
    expect(postPipelineActionApi).not.toHaveBeenCalled();
    expect(relayPipelineMessageApi).not.toHaveBeenCalled();
  });

  it("existing pipeline state → the message goes through the single-writer relay (never start)", async () => {
    vi.mocked(relayPipelineMessageApi).mockResolvedValue({ deferred: false, board: {} as PipelineBoard });
    wsMock.board = { state: { status: "awaiting_manazer" }, recent_messages: [] } as unknown as PipelineBoard;

    render(<ControlCenterPage />);
    fireEvent.click(screen.getByRole("button", { name: "send" }));

    await waitFor(() => expect(relayPipelineMessageApi).toHaveBeenCalledWith("v-1", SEND_TEXT));
    // A live pipeline must NOT re-run start.
    expect(postPipelineActionApi).not.toHaveBeenCalled();
  });
});

describe("ControlCenterPage — the step to Verifikácia waits for CI (DEV-261)", () => {
  it("shows why „Prejsť na overenie“ is held while the CI of the pushed commit runs", async () => {
    wsMock.board = {
      state: { current_stage: "programovanie", status: "awaiting_manazer", mode: null },
      recent_messages: [],
      available_actions: ["ask", "uprav"],
      action_labels: { schvalit: "Prejsť na overenie" }, // DEV-373: named by the cockpit
      ci_gate: { state: "pending", message: "Programovanie je hotové — čaká sa na kontroly na GitHube." },
    } as unknown as PipelineBoard;

    render(<ControlCenterPage />);

    expect(await screen.findByText("Čakám na kontroly na GitHube")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prejsť na overenie" })).toBeDisabled();
  });
});
