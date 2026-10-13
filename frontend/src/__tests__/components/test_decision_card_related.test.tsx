/**
 * DEV-34 — the card says which other card its choice relates to and why.
 *
 * NEX Inbox 1.7.0, 08.10.2026: two decided cards contradicted each other (card 7 × 9, card 1 × 2) and nobody saw it,
 * until the next review or the Poradca found it. The agent now records the relation when writing the cards (`related`)
 * and the card shows it to the Manažér as the other card's number and question — when deciding, not after.
 */

import "@testing-library/jest-dom/vitest";
import { render as rtlRender, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import DecisionCardsBar from "@/components/riadiace/DecisionCardsBar";
import type { PipelineBoard } from "@/services/api/pipeline";

// Every card leads to Poradca (DEV-106) — it lives in the app's router.
const render = (ui: ReactElement) => rtlRender(ui, { wrapper: MemoryRouter });

vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

const OPTIONS = [
  { id: "a", label: "Možnosť A", recommended: true },
  { id: "b", label: "Možnosť B" },
];

function board(related: unknown[] | undefined): PipelineBoard {
  return {
    state: { current_stage: "navrh", status: "blocked", block_reason: "decision_needed" },
    available_actions: ["decide"],
    recent_messages: [
      {
        id: "m1",
        seq: 10,
        author: "ai_agent",
        recipient: "manazer",
        kind: "consultation",
        content: "Treba tvoje rozhodnutie.",
        payload: {
          consultation: {
            id: "c1",
            decisions: [
              { key: "vypadok", question: "Kedy je to výpadok?", options: OPTIONS, related },
              { key: "kopia", question: "Čo pri chýbajúcej kópii faktúry?", options: OPTIONS },
            ],
          },
        },
      },
    ],
  } as unknown as PipelineBoard;
}

describe("Decision card — related cards (DEV-34)", () => {
  it("names the other card by number and question and says why", () => {
    render(
      <DecisionCardsBar
        board={board([{ key: "kopia", why: "Nové podmienky výpadku nesmú zachytiť chybu miestnej kópie." }])}
        versionId="v1"
        onBoard={vi.fn()}
      />,
    );
    expect(
      screen.getByText(
        "Súvisí s kartou 2 („Čo pri chýbajúcej kópii faktúry?“): Nové podmienky výpadku nesmú zachytiť chybu miestnej kópie.",
      ),
    ).toBeInTheDocument();
  });

  it("a card without relations writes nothing of the kind", () => {
    render(<DecisionCardsBar board={board(undefined)} versionId="v1" onBoard={vi.fn()} />);
    expect(screen.queryByText(/Súvisí s kartou/)).not.toBeInTheDocument();
  });
});
