/**
 * PokracovatPoOpraveBar (ICCINT-13) — the screen the Manažér sees after our technical team fixes a NEX Studio
 * bug his build died on. Until now that build showed "rieši ju náš technický tím" forever, with no button
 * that could ever restart it.
 *
 * What is pinned here is what he can SEE and DO, not the component's shape:
 *  - it appears only for a build the backend says is released-after-a-fix (honest-by-construction, both
 *    conditions — the flag alone, or `pokracovat` alone, must not be enough: `pokracovat` is ALSO the
 *    ordinary paused-build resume, which belongs to the task-plan rail);
 *  - it says the bug was FIXED (not "we are on it") and repeats WHAT was fixed, verbatim;
 *  - its one button really fires `pokracovat` and adopts the board that comes back;
 *  - it shows up in ANY phase — a NEX Studio bug can strike in any of them.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PokracovatPoOpraveBar from "@/components/riadiace/PokracovatPoOpraveBar";
import { postPipelineActionApi } from "@/services/api/pipeline";
import type { PipelineBoard, PipelineMessage } from "@/services/api/pipeline";

vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

const FIX_NOTE = "Zamykanie portu opravené vo v4.0.58 — sandbox už naštartuje.";

function unblockMsg(content: string, seq: number): PipelineMessage {
  return {
    id: `m-${seq}`,
    author: "dedo",
    recipient: "ai_agent",
    kind: "answer",
    content,
    payload: { dedo_unblock: true },
    seq,
  } as unknown as PipelineMessage;
}

function board({
  actions = ["pokracovat"],
  released = true,
  stage = "priprava",
  messages = [] as PipelineMessage[],
}: {
  actions?: string[];
  released?: boolean;
  stage?: string;
  messages?: PipelineMessage[];
} = {}): PipelineBoard {
  return {
    state: { current_stage: stage, status: "awaiting_manazer", resume_after_framework_fix: released },
    recent_messages: messages,
    available_actions: actions,
  } as unknown as PipelineBoard;
}

const NEXT_BOARD = { state: { status: "agent_working" } } as unknown as PipelineBoard;

describe("PokracovatPoOpraveBar — one button after a NEX Studio fix", () => {
  beforeEach(() => {
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(postPipelineActionApi).mockResolvedValue(NEXT_BOARD);
  });

  it("renders NOTHING while the build is still blocked on the bug", () => {
    // The blocked screen belongs to NahlasitZnovaBar; offering "Pokračovať" there would invite the Manažér
    // to restart a build into the very version that is broken.
    const blocked = {
      state: { status: "blocked", block_reason: "framework_issue", resume_after_framework_fix: false },
      recent_messages: [],
      available_actions: ["nahlasit_znova"],
    } as unknown as PipelineBoard;
    const { container } = render(<PokracovatPoOpraveBar board={blocked} versionId="v-1" onBoard={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders NOTHING for an ordinary paused build that merely offers pokracovat", () => {
    // That resume is the task-plan rail's ("Pokračovať v stavbe"). Rendering here too would put two buttons
    // for the same verb on one screen.
    const { container } = render(
      <PokracovatPoOpraveBar board={board({ released: false })} versionId="v-1" onBoard={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders NOTHING when the backend no longer offers the action", () => {
    const { container } = render(
      <PokracovatPoOpraveBar board={board({ actions: ["ask", "uprav"] })} versionId="v-1" onBoard={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("says the bug was FIXED and repeats what was fixed, verbatim", () => {
    render(
      <PokracovatPoOpraveBar
        board={board({ messages: [unblockMsg(FIX_NOTE, 7)] })}
        versionId="v-1"
        onBoard={vi.fn()}
      />,
    );
    expect(screen.getByText(/sme opravili/)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(FIX_NOTE.slice(0, 30)))).toBeInTheDocument();
    // …and never the "we are still working on it" wording of the blocked screen it replaces.
    expect(screen.queryByText(/rieši ju náš technický tím/)).not.toBeInTheDocument();
  });

  it("shows the NEWEST fix note when several are on the feed", () => {
    render(
      <PokracovatPoOpraveBar
        board={board({ messages: [unblockMsg("Staršia oprava.", 3), unblockMsg(FIX_NOTE, 9)] })}
        versionId="v-1"
        onBoard={vi.fn()}
      />,
    );
    expect(screen.getByText(new RegExp(FIX_NOTE.slice(0, 30)))).toBeInTheDocument();
    expect(screen.queryByText(/Staršia oprava/)).not.toBeInTheDocument();
  });

  it("still renders (without the note) when the fix message is off the recent tail", () => {
    render(<PokracovatPoOpraveBar board={board()} versionId="v-1" onBoard={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Pokračovať/ })).toBeInTheDocument();
    expect(screen.queryByText(/Čo sa opravilo/)).not.toBeInTheDocument();
  });

  it.each(["priprava", "navrh", "vizual", "programovanie", "verifikacia"])(
    "offers exactly one button in phase %s",
    (stage) => {
      render(<PokracovatPoOpraveBar board={board({ stage })} versionId="v-1" onBoard={vi.fn()} />);
      expect(screen.getAllByRole("button")).toHaveLength(1);
      expect(screen.getByRole("button", { name: /Pokračovať/ })).toBeInTheDocument();
    },
  );

  it("click fires pokracovat and adopts the returned board", async () => {
    const onBoard = vi.fn();
    render(<PokracovatPoOpraveBar board={board()} versionId="v-42" onBoard={onBoard} />);
    fireEvent.click(screen.getByRole("button", { name: /Pokračovať/ }));
    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v-42", { action: "pokracovat" }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(NEXT_BOARD));
  });

  it("a failed click leaves the button usable and says so", async () => {
    vi.mocked(postPipelineActionApi).mockRejectedValue(new Error("boom"));
    const onBoard = vi.fn();
    render(<PokracovatPoOpraveBar board={board()} versionId="v-1" onBoard={onBoard} />);
    fireEvent.click(screen.getByRole("button", { name: /Pokračovať/ }));
    await waitFor(() => expect(screen.getByText(/Pokračovanie zlyhalo/)).toBeInTheDocument());
    expect(onBoard).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Pokračovať/ })).toBeEnabled();
  });
});
