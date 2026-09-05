/**
 * DedoProposalBar (ICCINT-24) — the screen that ended the retyping.
 *
 * The Director would ask Dedo to review the AI Agent's work; Dedo found real mistakes; and the only way
 * those mistakes reached the agent was the Director reading Dedo's text and typing it again into the
 * cockpit. Four times in one day. This bar is that text, already in the box, one click from the agent —
 * with the decision still entirely the Manažér's.
 *
 * What is pinned here is what he SEES and what pressing a button DOES, not the component's shape:
 *  - it appears only when the backend says a proposal is open (honest-by-construction — a bar that renders
 *    on an empty board would be a standing invitation to send nothing);
 *  - it shows Dedo's wording verbatim, says out loud that it is DEDO's and that the agent has not seen it,
 *    and says what the click will do — in Slovak, never as an engine verb;
 *  - the box is EDITABLE and what is sent is what is in it, not the original;
 *  - declining is a real option and sends nothing;
 *  - a refusal from the engine is shown and leaves the bar usable (the proposal is still open server-side).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DedoProposalBar from "@/components/riadiace/DedoProposalBar";
import { sendDedoProposalApi, rejectDedoProposalApi, getPipelineBoardApi } from "@/services/api/pipeline";
import type { PipelineBoard } from "@/services/api/pipeline";
import { ApiError } from "@/services/api";

vi.mock("@/services/api/pipeline", () => ({
  sendDedoProposalApi: vi.fn(),
  rejectDedoProposalApi: vi.fn(),
  getPipelineBoardApi: vi.fn(),
}));

// ICCINT-62: starting a fast fix creates a NEW version, so the bar must move the cockpit onto it. Mocked so
// the test asserts the bar ASKS for that — the hook itself is covered by the pages that use it.
const openVersionCockpit = vi.fn();
vi.mock("@/hooks/useOpenVersionCockpit", () => ({
  useOpenVersionCockpit: () => openVersionCockpit,
}));

const FINDING =
  "Úloha #4 nemá test na zápornú cenu, hoci špecifikácia ho žiada v §3.2. Doplň ho a spusti overenie znova.";

function board({
  proposal = true,
  action = "uprav",
  content = FINDING,
}: { proposal?: boolean; action?: string; content?: string } = {}): PipelineBoard {
  return {
    state: { status: "awaiting_manazer", current_stage: "priprava" },
    recent_messages: [],
    available_actions: ["ask", "uprav"],
    dedo_proposal: proposal
      ? {
          message_id: "m-1",
          content,
          proposed_action: action,
          created_at: "2026-08-23T10:00:00Z",
        }
      : null,
  } as unknown as PipelineBoard;
}

const NEXT_BOARD = { state: { status: "agent_working" }, dedo_proposal: null } as unknown as PipelineBoard;

describe("DedoProposalBar — a finding from the technical team, one click away", () => {
  beforeEach(() => {
    vi.mocked(sendDedoProposalApi).mockReset();
    vi.mocked(rejectDedoProposalApi).mockReset();
    vi.mocked(getPipelineBoardApi).mockReset();
    vi.mocked(sendDedoProposalApi).mockResolvedValue(NEXT_BOARD);
    vi.mocked(rejectDedoProposalApi).mockResolvedValue(NEXT_BOARD);
    vi.mocked(getPipelineBoardApi).mockResolvedValue(NEXT_BOARD);
  });

  it("renders NOTHING when there is no proposal", () => {
    const { container } = render(
      <DedoProposalBar board={board({ proposal: false })} versionId="v-1" onBoard={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders NOTHING before the board has loaded", () => {
    const { container } = render(<DedoProposalBar board={null} versionId="v-1" onBoard={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names Dedo, says the agent has NOT seen it, and shows his wording verbatim", () => {
    render(<DedoProposalBar board={board()} versionId="v-1" onBoard={vi.fn()} />);

    expect(screen.getByText(/Dedo \(náš technický tím\)/)).toBeInTheDocument();
    expect(screen.getByText(/NEODIŠIEL/)).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue(FINDING);
  });

  it.each([
    ["uprav", /Vrátiť agentovi na doplnenie/, /vráti sa k rozrobenej práci/],
    ["answer", /Odpovedať agentovi/, /odpoveď na otázku/],
    ["ask", /Spýtať sa agenta/, /ako otázka/],
  ])("for %s it says in plain Slovak what the click will do", (action, button, effect) => {
    render(<DedoProposalBar board={board({ action })} versionId="v-1" onBoard={vi.fn()} />);
    expect(screen.getByRole("button", { name: button })).toBeInTheDocument();
    expect(screen.getByText(effect)).toBeInTheDocument();
  });

  it("sends Dedo's text unchanged when the Manažér does not touch it", async () => {
    const onBoard = vi.fn();
    render(<DedoProposalBar board={board()} versionId="v-42" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: /Vrátiť agentovi/ }));

    await waitFor(() => expect(sendDedoProposalApi).toHaveBeenCalledWith("v-42", "m-1", FINDING));
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(NEXT_BOARD));
  });

  it("sends what the Manažér EDITED, and says the edit will be what goes out", async () => {
    const edited = `${FINDING} Prosím, začni tým.`;
    render(<DedoProposalBar board={board()} versionId="v-1" onBoard={vi.fn()} />);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: edited } });
    expect(screen.getByText(/odošle sa tvoje znenie/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Vrátiť agentovi/ }));
    await waitFor(() => expect(sendDedoProposalApi).toHaveBeenCalledWith("v-1", "m-1", edited));
  });

  it("cannot send an empty box", () => {
    render(<DedoProposalBar board={board()} versionId="v-1" onBoard={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: /Vrátiť agentovi/ })).toBeDisabled();
    expect(sendDedoProposalApi).not.toHaveBeenCalled();
  });

  it("declining sends nothing and adopts the board that comes back", async () => {
    const onBoard = vi.fn();
    render(<DedoProposalBar board={board()} versionId="v-7" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: /Odmietnuť návrh/ }));

    await waitFor(() => expect(rejectDedoProposalApi).toHaveBeenCalledWith("v-7", "m-1"));
    expect(sendDedoProposalApi).not.toHaveBeenCalled();
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(NEXT_BOARD));
  });

  it("a refusal from the engine is shown and the bar stays usable", async () => {
    // e.g. "answer" on a build that is asking nothing — the action's own guard, which the send inherits.
    vi.mocked(sendDedoProposalApi).mockRejectedValue(new Error("boom"));
    const onBoard = vi.fn();
    render(<DedoProposalBar board={board()} versionId="v-1" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: /Vrátiť agentovi/ }));

    await waitFor(() => expect(screen.getByText(/Odoslanie návrhu zlyhalo/)).toBeInTheDocument());
    expect(onBoard).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Vrátiť agentovi/ })).toBeEnabled();
  });

  // ── the finding changed under him (audit 2026-08-23, finding 1) ────────────
  //
  // The server no longer acts on "whatever is open now" — both buttons carry the id of the proposal on
  // screen, so a finding Dedo wrote during the 25s reconcile window cannot be executed under a button the
  // Manažér pressed for something else. The refusal that follows is the cockpit's problem to finish: show
  // him what actually happened, and put the CURRENT finding in front of him instead of the stale one.

  it("names the proposal on screen, so the server can never act on a different one", async () => {
    render(<DedoProposalBar board={board()} versionId="v-1" onBoard={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /Vrátiť agentovi/ }));

    await waitFor(() => expect(sendDedoProposalApi).toHaveBeenCalledWith("v-1", "m-1", FINDING));
  });

  it("when the finding changed under him, it says so and swaps in the current one", async () => {
    const NEWER = "Dedo medzitým našiel niečo iné.";
    vi.mocked(sendDedoProposalApi).mockRejectedValue(
      new ApiError(409, "Technický tím medzitým poslal novší návrh — na obrazovke máš starý."),
    );
    vi.mocked(getPipelineBoardApi).mockResolvedValue(board({ content: NEWER }));
    const onBoard = vi.fn();
    render(<DedoProposalBar board={board()} versionId="v-1" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: /Vrátiť agentovi/ }));

    // The server's own sentence — not a canned "409" he cannot correlate with anything.
    await waitFor(() => expect(screen.getByText(/novší návrh/)).toBeInTheDocument());
    // …and the screen is put right by the app, not by asking him to reload it.
    await waitFor(() => expect(getPipelineBoardApi).toHaveBeenCalledWith("v-1"));
    expect(onBoard).toHaveBeenCalledWith(expect.objectContaining({ dedo_proposal: expect.anything() }));
  });

  it("the explanation survives the proposal being swapped underneath it", async () => {
    const NEWER = "Dedo medzitým našiel niečo iné.";
    vi.mocked(rejectDedoProposalApi).mockRejectedValue(new ApiError(409, "Tento návrh už bol odmietnutý."));
    vi.mocked(getPipelineBoardApi).mockResolvedValue(board({ content: NEWER }));
    const { rerender } = render(<DedoProposalBar board={board()} versionId="v-1" onBoard={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /Odmietnuť návrh/ }));
    await waitFor(() => expect(screen.getByText(/už bol odmietnutý/)).toBeInTheDocument());

    // The cockpit adopts the fresh board (a DIFFERENT proposal id) — the reason must not vanish with it.
    rerender(
      <DedoProposalBar
        board={{ ...board({ content: NEWER }), dedo_proposal: { message_id: "m-2", content: NEWER, proposed_action: "uprav", created_at: "2026-08-23T10:05:00Z" } } as unknown as PipelineBoard}
        versionId="v-1"
        onBoard={vi.fn()}
      />,
    );

    expect(screen.getByText(/už bol odmietnutý/)).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue(NEWER);
  });

  it("a board refresh does not wipe what the Manažér is typing", () => {
    // The cockpit re-fetches the board every 25s. The SAME proposal coming back must not clobber his edit.
    const { rerender } = render(<DedoProposalBar board={board()} versionId="v-1" onBoard={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "rozpísané…" } });

    rerender(<DedoProposalBar board={board()} versionId="v-1" onBoard={vi.fn()} />);

    expect(screen.getByRole("textbox")).toHaveValue("rozpísané…");
  });
});

describe("ICCINT-62 — a fast fix moves the cockpit onto the build it started", () => {
  beforeEach(() => {
    vi.mocked(sendDedoProposalApi).mockReset();
    openVersionCockpit.mockReset();
  });

  it("opens the NEW version after starting a fast fix", async () => {
    vi.mocked(sendDedoProposalApi).mockResolvedValue({
      state: { status: "agent_working", version_id: "v-new" },
      dedo_proposal: null,
    } as unknown as PipelineBoard);

    render(<DedoProposalBar board={board({ action: "fast_fix" })} versionId="v-old" onBoard={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /rýchlu opravu/i }));

    await waitFor(() => expect(openVersionCockpit).toHaveBeenCalledWith("v-new"));
  });

  it("stays put for the verbs that continue THIS build", async () => {
    vi.mocked(sendDedoProposalApi).mockResolvedValue({
      state: { status: "agent_working", version_id: "v-old" },
      dedo_proposal: null,
    } as unknown as PipelineBoard);

    render(<DedoProposalBar board={board({ action: "uprav" })} versionId="v-old" onBoard={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /vrátiť agentovi/i }));

    await waitFor(() => expect(sendDedoProposalApi).toHaveBeenCalled());
    expect(openVersionCockpit).not.toHaveBeenCalled();
  });
});
