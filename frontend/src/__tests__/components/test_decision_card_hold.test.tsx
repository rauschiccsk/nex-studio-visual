/**
 * DEV-107 — a held verification card stays on the screen, and the text in its guidance field stays with it.
 *
 * Until 4.47.0 the hold option disappeared the card: any recorded answer counted as decided, so the screen hid the
 * card the backend was still waiting on — nothing left to click. The backend now records whether an answer holds
 * (``held``) and the screen keeps the card for the decision still to come.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({ postPipelineActionApi: vi.fn() }));
vi.mock("@/services/api/pipeline", () => api);

import DecisionCardsBar from "@/components/riadiace/DecisionCardsBar";
import type { PipelineBoard } from "@/services/api/pipeline";

const VERSION = "v180";
const QUESTION = "Verifikácia našla blokujúcu chybu. Ako chceš pokračovať?";
const NOTE = "Skús najprv overiť, či zlyhanie nespôsobilo zostavenie.";
const NOTE_KEY = `nex.draft.karta.verifikacia_fix_next.${VERSION}`;

const CARD = {
  id: "m1",
  version_id: VERSION,
  stage: "verifikacia",
  author: "system",
  recipient: "manazer",
  kind: "consultation",
  content: "Verifikácia našla chybu.",
  status: "sent",
  seq: 10,
  created_at: "2026-10-13T10:00:00Z",
  payload: {
    consultation: {
      id: "verifikacia-fix-v180-1",
      source: "verifikacia_fix",
      decisions: [
        {
          key: "verifikacia_fix_next",
          question: QUESTION,
          options: [
            { id: "fix_it", label: "Nechaj to opraviť", recommended: true },
            { id: "hold", label: "Zatiaľ podržať (rozhodnem neskôr)" },
          ],
        },
      ],
    },
  },
};

function answer(seq: number, optionId: string, held: boolean) {
  return {
    ...CARD,
    id: `a${seq}`,
    seq,
    kind: "answer",
    author: "manazer",
    payload: {
      consultation_decision: { key: "verifikacia_fix_next", option_id: optionId, label: optionId, held },
    },
  };
}

function board(...answers: unknown[]): PipelineBoard {
  return {
    available_actions: ["decide"],
    state: { status: "blocked", block_reason: "decision_needed", current_stage: "programovanie" },
    recent_messages: [CARD, ...answers],
  } as unknown as PipelineBoard;
}

function bar(b: PipelineBoard, onBoard = vi.fn()) {
  return render(
    <MemoryRouter>
      <DecisionCardsBar board={b} versionId={VERSION} onBoard={onBoard} />
    </MemoryRouter>,
  );
}

const noteBox = () => screen.getByPlaceholderText(/Pokyn pre AI partnera/) as HTMLTextAreaElement;

beforeEach(() => {
  api.postPipelineActionApi.mockReset();
  window.localStorage.clear();
});

describe("DEV-107 — holding a verification card", () => {
  it("a held card stays on the screen, ready for the decision, and says it waits", () => {
    bar(board(answer(11, "hold", true)));

    expect(screen.getByText(QUESTION)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Nechaj to opraviť/ })).toBeEnabled();
    expect(screen.getByText("Podržané — karta počká na tvoje rozhodnutie.")).toBeInTheDocument();
  });

  it("an answer that decides still closes the card", () => {
    bar(board(answer(11, "hold", true), answer(12, "fix_it", false)));

    expect(screen.queryByText(QUESTION)).not.toBeInTheDocument();
  });

  it("holding with text in the field keeps the text there — it was not sent", async () => {
    window.localStorage.setItem(NOTE_KEY, NOTE);
    const onBoard = vi.fn();
    api.postPipelineActionApi.mockResolvedValue(board(answer(11, "hold", true)));
    const { rerender } = bar(board(), onBoard);

    fireEvent.click(screen.getByRole("button", { name: /Zatiaľ podržať/ }));
    fireEvent.click(screen.getByRole("button", { name: /Rozhodnúť/ }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledTimes(1));
    rerender(
      <MemoryRouter>
        <DecisionCardsBar board={onBoard.mock.lastCall?.[0]} versionId={VERSION} onBoard={onBoard} />
      </MemoryRouter>,
    );

    expect(noteBox()).toHaveValue(NOTE);
    expect(window.localStorage.getItem(NOTE_KEY)).toBe(NOTE);
    expect(screen.getByText("Text v poli sa neposlal — ostáva tu na neskôr.")).toBeInTheDocument();
    expect(api.postPipelineActionApi).toHaveBeenCalledWith(VERSION, {
      action: "decide",
      payload: { decision_key: "verifikacia_fix_next", option_id: "hold", note: NOTE },
    });
  });

  it.each([
    ["first", [], [answer(11, "fix_it", false)]],
    ["after a hold", [answer(11, "hold", true)], [answer(11, "hold", true), answer(12, "fix_it", false)]],
  ])("a decision that goes through clears the field — %s", async (_when, before, after) => {
    window.localStorage.setItem(NOTE_KEY, NOTE);
    const onBoard = vi.fn();
    api.postPipelineActionApi.mockResolvedValue(board(...after));
    bar(board(...before), onBoard);

    fireEvent.click(screen.getByRole("button", { name: /Nechaj to opraviť/ }));
    fireEvent.click(screen.getByRole("button", { name: /Rozhodnúť/ }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledTimes(1));

    expect(window.localStorage.getItem(NOTE_KEY)).toBeNull();
  });
});
