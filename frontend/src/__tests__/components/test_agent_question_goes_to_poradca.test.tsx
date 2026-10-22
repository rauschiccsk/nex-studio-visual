/**
 * DEV-135 — the agent's question in Riadiace centrum goes to Poradca with one click, as a decision card does, and the
 * answer Poradca recommends comes back into the answer box with one click; the Manažér sends it himself.
 *
 * Director 16.10.2026 at the agent's question in Príprava of NEX Inbox 1.9.0: „Chýba mi tu tlačidlo ktorým odošleme
 * pre Poradcu.“ The question had to be copied into Poradca by hand, and Poradca's recommended answer („Odpovedz
 * agentovi …“) had no way back into the box — only an instruction for the agent had one.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

vi.mock("@/services/api/pipeline", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  postPipelineActionApi: vi.fn(),
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({
      selectedProject: { slug: "nex-inbox" },
      setSelectedVersion: vi.fn(),
    }),
}));
vi.mock("@/services/api/poradca", () => ({
  stopPoradcaApi: vi.fn(),
  saveRequestToBacklogApi: vi.fn(),
}));

import BlockRecoveryBar from "@/components/riadiace/BlockRecoveryBar";
import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";
import { takeHandoff } from "@/lib/poradcaHandoff";
import { reviewQuestion, takeReview } from "@/lib/poradcaReview";
import type { PipelineBoard, PipelineMessage } from "@/services/api/pipeline";
import type { PoradcaChoice } from "@/types/poradca";

const QUESTION =
  "Ktorý zoznam dodávateľov je platný — ten v Zadaní, alebo ten v prílohe?";

function message(
  seq: number,
  author: string,
  payload: Record<string, unknown> | null,
  content = "x",
): PipelineMessage {
  return {
    id: `m${seq}`,
    version_id: "v1",
    stage: "priprava",
    author,
    recipient: "manazer",
    kind: "question",
    content,
    status: "sent",
    payload,
    created_at: "2026-10-16T10:00:00Z",
    seq,
  } as PipelineMessage;
}

const ASKED = message(7, "ai_agent", {
  report: "Našiel som dva zoznamy dodávateľov a líšia sa v troch firmách.",
  question: QUESTION,
});

function board(
  reason: string,
  messages: PipelineMessage[],
  nextAction = `AI partner sa pýta: ${QUESTION}`,
): PipelineBoard {
  return {
    state: {
      status: "blocked",
      block_reason: reason,
      current_stage: "priprava",
      next_action: nextAction,
    },
    recent_messages: messages,
  } as unknown as PipelineBoard;
}

function Where() {
  const at = useLocation();
  return <p>na adrese {at.pathname + at.search}</p>;
}

function renderBar(b: PipelineBoard) {
  render(
    <MemoryRouter initialEntries={["/riadiace-centrum"]}>
      <Routes>
        <Route
          path="/riadiace-centrum"
          element={
            <BlockRecoveryBar board={b} versionId="v1" onBoard={vi.fn()} />
          }
        />
        <Route path="/poradca" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => window.localStorage.clear());

describe("the agent's question goes to Poradca", () => {
  it("the agent's question has the button", () => {
    renderBar(board("agent_question", [ASKED]));
    expect(
      screen.getByRole("button", { name: "Overiť s Poradcom" }),
    ).toBeInTheDocument();
  });

  it("an error the Manažér retries has no question for Poradca", () => {
    renderBar(board("agent_error", [], "Agent zlyhal — skús znova."));
    expect(
      screen.getByRole("button", { name: "Skús znova" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Overiť s Poradcom" }),
    ).not.toBeInTheDocument();
  });

  it("a click hands the question with what the agent found to Poradca, opened on the version", () => {
    renderBar(board("agent_question", [message(3, "manazer", null), ASKED]));

    fireEvent.click(screen.getByRole("button", { name: "Overiť s Poradcom" }));

    expect(
      screen.getByText("na adrese /poradca?verzia=v1&overit=1"),
    ).toBeInTheDocument();
    const card = takeReview("v1");
    expect(card && reviewQuestion(card, 20_000).split("\n")).toEqual([
      "Otázka agenta. Stavba stojí vo fáze Príprava a čaká na moju odpoveď.",
      "",
      QUESTION,
      "",
      "Pred otázkou agent napísal:",
      "Našiel som dva zoznamy dodávateľov a líšia sa v troch firmách.",
      "",
      "Poraď mi, ako agentovi odpovedať.",
    ]);
  });

  it("a question the Manažér already answered is not taken for the open one — the build's own sentence is", () => {
    renderBar(
      board(
        "agent_question",
        [ASKED, message(8, "manazer", null)],
        "AI partner sa pýta: Môžem pokračovať?",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Overiť s Poradcom" }));

    const card = takeReview("v1");
    expect(card && reviewQuestion(card, 20_000).split("\n")).toEqual([
      "Otázka agenta. Stavba stojí vo fáze Príprava a čaká na moju odpoveď.",
      "",
      "AI partner sa pýta: Môžem pokračovať?",
      "",
      "Poraď mi, ako agentovi odpovedať.",
    ]);
  });

  it("a database change asks whether to accept it, with the change the build describes", () => {
    const change = message(
      9,
      "system",
      { phase: "priprava", database_schema: {} },
      "Návrh mení štruktúru databázy. Pribudne tabuľka suppliers.",
    );
    renderBar(
      board(
        "schema_approval",
        [ASKED, change],
        "Návrh mení štruktúru databázy. Schváliť ju smie len Ri.",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Overiť s Poradcom" }));

    const card = takeReview("v1");
    expect(card && reviewQuestion(card, 20_000).split("\n")).toEqual([
      "Otázka agenta. Stavba stojí vo fáze Príprava a čaká na schválenie zmeny databázy.",
      "",
      "Návrh mení štruktúru databázy. Schváliť ju smie len Ri.",
      "",
      "Zmena, ktorú stavba navrhuje:",
      "Návrh mení štruktúru databázy. Pribudne tabuľka suppliers.",
      "",
      "Poraď mi, či túto zmenu databázy prijať; ak nie, ako agentovi odpovedať.",
    ]);
  });
});

const ANSWER: PoradcaChoice = {
  card: null,
  label: "Platný je zoznam v prílohe.",
  status: "answer",
  question: null,
};
const ON_CARD: PoradcaChoice = {
  card: 2,
  label: "Zhasnúť hneď",
  status: "on_card",
  question: "Q",
};
const VERSION = {
  id: "v1",
  version_number: "1.9.0",
  instruction_open: true,
  instruction_closed_reason: null,
};

function renderAnswer(
  choices: PoradcaChoice[],
  scopeVersion: unknown = VERSION,
) {
  const msg = {
    id: "p1",
    author: "poradca",
    content: "Odpovedz agentovi <volba>Platný je zoznam v prílohe.</volba>",
    steps: [],
    status: "done",
    instruction: null,
    backlog_request: null,
    captured_backlog_number: null,
    choices,
  };
  render(
    <MemoryRouter initialEntries={["/poradca"]}>
      <Routes>
        <Route
          path="/poradca"
          element={
            <PoradcaAnswer
              message={msg as never}
              scopeVersion={scopeVersion as never}
            />
          }
        />
        <Route path="/riadiace-centrum" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Poradca's recommended answer comes back into the answer box", () => {
  it("one click puts the answer into the box of Riadiace centrum — nothing is sent", () => {
    renderAnswer([ANSWER]);

    fireEvent.click(screen.getByRole("button", { name: "Vložiť do odpovede" }));

    expect(screen.getByText("na adrese /riadiace-centrum")).toBeInTheDocument();
    expect(takeHandoff("v1")).toBe("Platný je zoznam v prílohe.");
  });

  it("a choice on a decision card is picked on the card, not inserted", () => {
    renderAnswer([ON_CARD]);
    expect(
      screen.queryByRole("button", { name: "Vložiť do odpovede" }),
    ).not.toBeInTheDocument();
  });

  it("when the box would not take it, the button says why instead of sending him to an empty box", () => {
    renderAnswer([ANSWER], null);

    const button = screen.getByRole("button", { name: "Vložiť do odpovede" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute(
      "title",
      expect.stringMatching(/hore vyber verziu/),
    );
  });
});
