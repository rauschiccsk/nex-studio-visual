/**
 * DEV-22 — „Vložiť do Riadiaceho centra" must put Poradca's instruction where the Manažér can SEE it.
 *
 * Director 08.10.2026, NEX Inbox 1.7.0 waiting on an agent question: he clicked the button, the screen
 * blinked, and nothing happened. The handoff wrote into the conversation box's draft, but while the agent
 * waits on a question that box is collapsed to a one-line pointer and the block-recovery bar takes the
 * text. The old Poradca test could not see it: it routed /riadiace-centrum to an empty <div>.
 *
 * These tests walk his exact path — the real button in PoradcaAnswer, then the REAL Riadiace centrum page
 * (its two input boxes are not stubbed) — and ask which box shows the instruction.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { PipelineBoard } from "@/services/api/pipeline";

const h = vi.hoisted(() => ({
  ws: { board: null as unknown, activity: [] as unknown[], reconnecting: false, error: null as string | null },
  ctx: {
    selectedProject: { slug: "nex-inbox", name: "NEX Inbox" } as { slug: string; name: string } | null,
    selectedVersion: null as { versionId: string; versionNumber: string } | null,
    setSelectedVersion: (v: { versionId: string; versionNumber: string }) => {
      h.ctx.selectedVersion = v;
    },
  },
  postPipelineActionApi: vi.fn(),
}));

vi.mock("@/hooks/usePipelineWs", () => ({
  usePipelineWs: () => ({ ...h.ws, setBoard: vi.fn() }),
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: typeof h.ctx) => unknown) => sel(h.ctx),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ token: "t", user: { id: "me", role: "ri" } }),
}));
vi.mock("@/services/api/pipeline", () => ({
  relayPipelineMessageApi: vi.fn(),
  postPipelineActionApi: h.postPipelineActionApi,
  getCiStatusApi: vi.fn().mockResolvedValue({ stav: "unknown", detail: "—", sha: null }),
}));
vi.mock("@/services/api/poradca", () => ({
  stopPoradcaApi: vi.fn(),
  saveRequestToBacklogApi: vi.fn(),
}));
// Heavy read-only parts of the page — irrelevant to which box takes the text.
vi.mock("@/components/riadiace/ConversationThread", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/SpecApprovalBar", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/PhaseBar", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/HonestStatusStrip", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/PlanUlohRail", () => ({ default: () => <div /> }));

import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";
import { BLOCKED_INPUT_OWNER, type InputOwner } from "@/components/riadiace/blockRecovery";
import RiadiaceCentrumPage from "@/pages/RiadiaceCentrumPage";

const VERSION = "v170";
const INSTRUCTION = "Odpovedz agentovi: NIB-046 rieši obsluha v detaile faktúry.";
const ANSWER_PLACEHOLDER = /Tvoja odpoveď/;
const CHAT_PLACEHOLDER = /Napíš AI Agentovi/;

function board(status: string, blockReason: string | null = null): PipelineBoard {
  return {
    state: { status, block_reason: blockReason, current_stage: "priprava", next_action: "Agent sa pýta." },
  } as unknown as PipelineBoard;
}

const MESSAGE = {
  id: "m1",
  author: "poradca",
  content: `Odpoveď.\n<pokyn-pre-agenta>${INSTRUCTION}</pokyn-pre-agenta>`,
  steps: [],
  status: "done",
  instruction: INSTRUCTION,
  backlog_request: null,
  captured_backlog_number: null,
};
const SCOPE = {
  id: VERSION,
  version_number: "1.7.0",
  stage: "priprava",
  status: "blocked",
  instruction_open: true,
  instruction_closed_reason: null,
};

function tree(start = "/poradca") {
  return (
    <MemoryRouter initialEntries={[start]}>
      <Routes>
        <Route
          path="/poradca"
          element={<PoradcaAnswer message={MESSAGE as never} scopeVersion={SCOPE as never} />}
        />
        <Route path="/riadiace-centrum" element={<RiadiaceCentrumPage />} />
      </Routes>
    </MemoryRouter>
  );
}

function openRiadiaceCentrum() {
  h.ctx.selectedVersion = { versionId: VERSION, versionNumber: "1.7.0" };
  return render(tree("/riadiace-centrum"));
}

function clickInsert() {
  fireEvent.click(screen.getByRole("button", { name: /Vložiť do Riadiaceho centra/ }));
}

beforeEach(() => {
  window.localStorage.clear();
  h.ctx.selectedVersion = null;
  h.ws.board = null;
  h.postPipelineActionApi.mockReset();
});

describe("DEV-22 — Poradca's instruction lands in the box that takes the text", () => {
  it("agent waits on a question: the instruction is in „Tvoja odpoveď…“, labelled as Poradca's", async () => {
    h.ws.board = board("blocked", "agent_question");
    render(tree());
    clickInsert();
    const answer = await screen.findByPlaceholderText(ANSWER_PLACEHOLDER);
    await waitFor(() => expect(answer).toHaveValue(INSTRUCTION));
    expect(screen.getByText(/Pokyn od Poradcu/)).toBeInTheDocument();
    // The conversation box is collapsed — there is exactly one box with text in it.
    expect(screen.queryByPlaceholderText(CHAT_PLACEHOLDER)).not.toBeInTheDocument();
    expect(screen.getByText("Pokračuj cez lištu vyššie.")).toBeInTheDocument();
  });

  it("agent works: the instruction is in the conversation box, under the Manažér's own draft", async () => {
    window.localStorage.setItem(`nex.draft.rozhovor.${VERSION}`, "moja rozpísaná veta");
    h.ws.board = board("agent_working");
    render(tree());
    clickInsert();
    const chat = await screen.findByPlaceholderText(CHAT_PLACEHOLDER);
    await waitFor(() => expect(chat).toHaveValue(`moja rozpísaná veta\n\n${INSTRUCTION}`));
    expect(screen.getByText(/Pokyn od Poradcu/)).toBeInTheDocument();
    expect(h.postPipelineActionApi).not.toHaveBeenCalled();
  });

  it("while the board is still loading nobody grabs it; the bar that takes the input gets it once it is known", async () => {
    h.ws.board = null;
    const { rerender } = render(tree());
    clickInsert();
    // The board is unknown: the conversation box is on screen, but must not take the instruction yet.
    expect(await screen.findByPlaceholderText(CHAT_PLACEHOLDER)).toHaveValue("");
    h.ws.board = board("blocked", "agent_question");
    rerender(tree());
    const answer = await screen.findByPlaceholderText(ANSWER_PLACEHOLDER);
    await waitFor(() => expect(answer).toHaveValue(INSTRUCTION));
  });

  it("taken while the agent worked, then the agent asked: the instruction moves up, it does not hide below", async () => {
    h.ws.board = board("agent_working");
    const { rerender } = render(tree());
    clickInsert();
    await waitFor(() => expect(screen.getByPlaceholderText(CHAT_PLACEHOLDER)).toHaveValue(INSTRUCTION));
    h.ws.board = board("blocked", "agent_question");
    rerender(tree());
    const answer = await screen.findByPlaceholderText(ANSWER_PLACEHOLDER);
    await waitFor(() => expect(answer).toHaveValue(INSTRUCTION));
    expect(window.localStorage.getItem(`nex.draft.rozhovor.${VERSION}`)).toBeNull();
  });

  it("once he edits the answer, the label goes — and a sent answer forgets where it came from", async () => {
    h.ws.board = board("blocked", "agent_question");
    h.postPipelineActionApi.mockResolvedValue(board("agent_working"));
    render(tree());
    clickInsert();
    const answer = await screen.findByPlaceholderText(ANSWER_PLACEHOLDER);
    await waitFor(() => expect(answer).toHaveValue(INSTRUCTION));
    fireEvent.change(answer, { target: { value: `${INSTRUCTION} Ďakujem.` } });
    expect(screen.queryByText(/Pokyn od Poradcu/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Odpovedať" }));
    await waitFor(() =>
      expect(h.postPipelineActionApi).toHaveBeenCalledWith(VERSION, {
        action: "answer",
        payload: { text: `${INSTRUCTION} Ďakujem.` },
      }),
    );
    expect(window.localStorage.getItem(`nex.draft.odpoved.${VERSION}.origin`)).toBeNull();
  });
});

describe("DEV-22 — the Director's own case after the fix is deployed", () => {
  it("an instruction parked in the collapsed box by the old screen + a new click: it shows up once, in the answer", async () => {
    // What the old handoff left in his browser on 08.10.2026: Poradca's text in the (collapsed) conversation box.
    window.localStorage.setItem(`nex.draft.rozhovor.${VERSION}`, INSTRUCTION);
    window.localStorage.setItem(`nex.draft.rozhovor.${VERSION}.origin`, "poradca");
    h.ws.board = board("blocked", "agent_question");
    render(tree());
    clickInsert();
    const answer = await screen.findByPlaceholderText(ANSWER_PLACEHOLDER);
    await waitFor(() => expect(answer).toHaveValue(INSTRUCTION));
    expect(window.localStorage.getItem(`nex.draft.rozhovor.${VERSION}`)).toBeNull();
  });

  it("an answer sent unedited forgets that it came from Poradca", async () => {
    h.ws.board = board("blocked", "agent_question");
    h.postPipelineActionApi.mockResolvedValue(board("agent_working"));
    render(tree());
    clickInsert();
    const answer = await screen.findByPlaceholderText(ANSWER_PLACEHOLDER);
    await waitFor(() => expect(answer).toHaveValue(INSTRUCTION));
    expect(window.localStorage.getItem(`nex.draft.odpoved.${VERSION}.origin`)).toBe("poradca");
    fireEvent.click(screen.getByRole("button", { name: "Odpovedať" }));
    await waitFor(() => expect(h.postPipelineActionApi).toHaveBeenCalled());
    await waitFor(() => expect(window.localStorage.getItem(`nex.draft.odpoved.${VERSION}.origin`)).toBeNull());
  });
});

// ── DEV-26 / DEV-27 — a consultation: the Decision Card takes the instruction, the chat only asks ─────────────

const NOTE_PLACEHOLDER = /Pokyn pre AI partnera/;

function consultationBoard(): PipelineBoard {
  return {
    state: {
      status: "blocked",
      block_reason: "decision_needed",
      current_stage: "navrh",
      next_action: "Manažér: rozhodni 1/2 (2 rozhodnutia, konzultácia — kolo 2 z 5).",
    },
    available_actions: ["decide", "ask"],
    recent_messages: [
      {
        id: "k1",
        seq: 10,
        kind: "consultation",
        author: "ai_agent",
        recipient: "manazer",
        content: "Previerka našla dva body.",
        payload: {
          consultation: {
            id: "navrh-2",
            round: 2,
            round_max: 5,
            decisions: [
              {
                key: "velke",
                question: "Čo s veľkou faktúrou?",
                options: [
                  { id: "dlhsi", label: "Dlhší limit", recommended: true },
                  { id: "nechat", label: "Nechať tak" },
                ],
              },
              {
                key: "sablona",
                question: "Čo so šablónou?",
                options: [{ id: "povinny", label: "Povinný priečinok", recommended: true }],
              },
            ],
          },
        },
      },
    ],
  } as unknown as PipelineBoard;
}

describe("DEV-26 — during a consultation Poradca's instruction lands on the Decision Card", () => {
  it("the instruction is in the card's „Pokyn pre AI partnera“, labelled as Poradca's; the chat stays empty", async () => {
    h.ws.board = consultationBoard();
    render(tree());
    clickInsert();
    const note = await screen.findByPlaceholderText(NOTE_PLACEHOLDER);
    await waitFor(() => expect(note).toHaveValue(INSTRUCTION));
    expect(screen.getByText(/Pokyn od Poradcu/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(CHAT_PLACEHOLDER)).toHaveValue("");
  });

  it("a multi-line instruction keeps its lines in the card", async () => {
    h.ws.board = consultationBoard();
    render(tree());
    window.localStorage.setItem(`nex.poradca.pending.${VERSION}`, "1. prvý bod\n2. druhý bod");
    clickInsert();
    const note = await screen.findByPlaceholderText(NOTE_PLACEHOLDER);
    await waitFor(() => expect(note).toHaveValue(`1. prvý bod\n2. druhý bod\n\n${INSTRUCTION}`));
    expect(note.tagName).toBe("TEXTAREA");
  });

  it("deciding the card sends Poradca's instruction as the card's note — and forgets where it came from", async () => {
    h.ws.board = consultationBoard();
    h.postPipelineActionApi.mockResolvedValue(consultationBoard());
    render(tree());
    clickInsert();
    await waitFor(async () => expect(await screen.findByPlaceholderText(NOTE_PLACEHOLDER)).toHaveValue(INSTRUCTION));
    fireEvent.click(screen.getByRole("button", { name: /Dlhší limit/ }));
    fireEvent.click(screen.getByRole("button", { name: /Rozhodnúť/ }));
    await waitFor(() =>
      expect(h.postPipelineActionApi).toHaveBeenCalledWith(VERSION, {
        action: "decide",
        payload: { decision_key: "velke", option_id: "dlhsi", note: INSTRUCTION },
      }),
    );
    await waitFor(() => expect(window.localStorage.getItem(`nex.draft.karta.${VERSION}.origin`)).toBeNull());
  });

  it("parked in the chat while the agent worked, then the consultation opened: it moves to the card", async () => {
    h.ws.board = board("agent_working");
    const { rerender } = render(tree());
    clickInsert();
    await waitFor(() => expect(screen.getByPlaceholderText(CHAT_PLACEHOLDER)).toHaveValue(INSTRUCTION));
    h.ws.board = consultationBoard();
    rerender(tree());
    await waitFor(async () => expect(await screen.findByPlaceholderText(NOTE_PLACEHOLDER)).toHaveValue(INSTRUCTION));
    await waitFor(() => expect(screen.getByPlaceholderText(CHAT_PLACEHOLDER)).toHaveValue(""));
  });
});

describe("DEV-27 — during a consultation the chat says it is for questions", () => {
  it("above the chat: decisions are made on the card, a question here changes no document", async () => {
    h.ws.board = consultationBoard();
    openRiadiaceCentrum();
    expect(await screen.findByText(/Prebieha konzultácia — rozhoduj na karte vyššie/)).toBeInTheDocument();
  });

  it("outside a consultation the sentence is not there", async () => {
    h.ws.board = board("agent_working");
    openRiadiaceCentrum();
    await screen.findByPlaceholderText(CHAT_PLACEHOLDER);
    expect(screen.queryByText(/Prebieha konzultácia/)).not.toBeInTheDocument();
  });
});

// The guard as a rule, not a list: EVERY reason a build can block on (the Record is exhaustive over the
// generated BlockReason, so a new reason cannot skip this). DEV-22 tested two states and missed the third.
const FIELD_OF: Record<InputOwner, RegExp> = {
  odpoved: /Tvoja odpoveď|Usmernenie k oprave/,
  karta: NOTE_PLACEHOLDER,
  rozhovor: CHAT_PLACEHOLDER,
};

function boxesHolding(text: string): HTMLElement[] {
  return screen
    .queryAllByRole("textbox")
    .filter((el) => (el as HTMLInputElement | HTMLTextAreaElement).value.includes(text));
}

describe("DEV-26 — every reason a build blocks on: the instruction is in the box that takes the text, or nowhere", () => {
  it.each(Object.entries(BLOCKED_INPUT_OWNER))("%s → %s", async (reason, owner) => {
    h.ws.board = reason === "decision_needed" ? consultationBoard() : board("blocked", reason);
    render(tree());
    clickInsert();
    await waitFor(() => expect(screen.queryByRole("button", { name: /Vložiť do Riadiaceho centra/ })).toBeNull());
    if (owner) {
      await waitFor(() => expect(screen.getByPlaceholderText(FIELD_OF[owner])).toHaveValue(INSTRUCTION));
      expect(boxesHolding(INSTRUCTION)).toHaveLength(1);
    } else {
      // No box takes text here: the instruction waits, untouched, for the box that will.
      expect(await screen.findByText("Túto chybu rieši náš technický tím.")).toBeInTheDocument();
      expect(boxesHolding(INSTRUCTION)).toHaveLength(0);
      expect(window.localStorage.getItem(`nex.poradca.pending.${VERSION}`)).toBe(INSTRUCTION);
    }
  });
});

